/******************************************************************************
 * ctcp.c
 * ------
 * Implementation of cTCP done here. This is the only file you need to change.
 * Look at the following files for references and useful functions:
 *   - ctcp.h: Headers for this file.
 *   - ctcp_iinked_list.h: Linked list functions for managing a linked list.
 *   - ctcp_sys.h: Connection-related structs and functions, cTCP segment
 *                 definition.
 *   - ctcp_utils.h: Checksum computation, getting the current time.
 *
 *****************************************************************************/

#include "ctcp_sys.h"
#include "ctcp.h"
#include "ctcp_linked_list.h"
#include "ctcp_utils.h"
/**
 * Connection state.
 *
 * Stores per-connection information such as the current sequence number,
 * unacknowledged packets, etc.
 *
 * You should add to this to store other fields you might need.
 */
struct ctcp_state {
  struct ctcp_state *next;  /* Next in linked list */
  struct ctcp_state **prev; /* Prev in linked list */

  conn_t *conn;             /* Connection object -- needed in order to figure
                               out destination when sending */
  linked_list_t *segments;  /* Linked list of segments sent to this connection.
                               It may be useful to have multiple linked lists
                               for unacknowledged segments, segments that
                               haven't been sent, etc. Lab 1 uses the
                               stop-and-wait protocol and therefore does not
                               necessarily need a linked list. You may remove
                               this if this is the case for you */

  /* FIXME: Add other needed fields. */
  struct bbr *bbr; /* need to store bbr parameters for each state */

  ctcp_config_t *cfg; /* store the values of the config here */
  FILE *bdp_fptr; /* the filepointer to write to bdp.txt */

  uint32_t init_seqno; /* initial sequence number */
  uint32_t curr_seqno; /* current value of sequnce number */
  uint32_t next_seqno; /* the next expected sequence number */

  uint32_t init_ackno; /* initial acknowledgement number */
  uint32_t curr_ackno; /* current acknowledgement number */
  uint32_t prev_ackno; /* previously sent out acknowledgement number */

  uint16_t send_window; /* size of the sender window */
  uint16_t recv_window; /* size of the receiver window */
  uint16_t sliding_window_size; /* the current bytes on the window */

  unsigned int conn_state; /* state of the connection */

  unsigned int last_timer_call; /* keeps track of when the ctcp_timer was called */

  long last_packet_send_time; /* keeps track of when the last packet was sent to do timeout */

  long min_seen_rtt; /* to store the min value of rtt */
  unsigned int max_seen_bw; /* to store the max value of the bottleneck bandwidth */

  unsigned int inflight_data; /* size of total data in flight */
  unsigned int app_limited_until; /*Indicates if the application is rate limited*/

  linked_list_t *unacked_segs; /*linked list to keep track of the unacked segments*/
  linked_list_t *segs_to_be_sent; /*linked list of segments that have to be sent*/
};

/**
 * Linked list of connection states. Go through this in ctcp_timer() to
 * resubmit segments and tear down connections.
 */
static ctcp_state_t *state_list;

/* FIXME: Feel free to add as many helper functions as needed. Don't repeat
          code! Helper functions make the code clearer and cleaner. */


ctcp_state_t *ctcp_init(conn_t *conn, ctcp_config_t *cfg) {
  /* Connection could not be established. */
  if (conn == NULL) {
    return NULL;
  }

  /* Established a connection. Create a new state and update the linked list
     of connection states. */
  struct ctcp_state *state = calloc(sizeof(ctcp_state_t), 1);
  state->next = state_list;
  state->prev = &state_list;
  if (state_list)
    state_list->prev = &state->next;
  state_list = state;

  /* Set fields. */
  state->conn = conn;
  /* FIXME: Do any other initialization here. */
  /* Initiate the seqno and ackno to 1 */
  state->init_seqno = 1;
  state->init_ackno = 1;

  /* current seqno and ackno are init seqno and ackno respectively */
  state->curr_seqno = state->init_seqno;
  state->curr_ackno = state->init_ackno;
  state->prev_ackno = state->init_ackno;

  /* set the state sender and receive window as the config send and receive window */
  state->recv_window = cfg->recv_window;
  state->send_window = cfg->send_window;

  /* initialize the remaining values */
  state->unacked_segs = ll_create();
  state->segs_to_be_sent = ll_create();
  state->conn_state = 0;
  state->sliding_window_size = 0;
  state->cfg = cfg;
  state->next_seqno = state->init_seqno;
  state->inflight_data = 0;
  state->last_timer_call =0;
  state->min_seen_rtt =-1;
  state->max_seen_bw = -1;
  state->app_limited_until=0;
  state->last_packet_send_time =0;

  /* open file */
  state->bdp_fptr  = fopen("bdp.txt","w");

  /* initialize bbr struct */
  state->bbr = bbr_init();

  /* initially set the cwnd of bbr to the send window size */
  state->bbr->cwnd = state->send_window;

  /* bbr will start in the BBR_STARTUP mode */
  state->bbr->mode = BBR_STARTUP;

  //free(cfg);
  return state;
}

void ctcp_destroy(ctcp_state_t *state) {
  unsigned int len, i;
  /* Update linked list. */
  if (state->next)
    state->next->prev = state->prev;

  *state->prev = state->next;
  conn_remove(state->conn);

  /* FIXME: Do any other cleanup here. */
  /* First clear the object from the list of segments to be sent and then remove the node itself */
  len = ll_length(state->unacked_segs);
  for(i = 0; i < len; i++)
  {
    ll_node_t *curr_node = ll_front(state->unacked_segs);
    free(curr_node->object);
    ll_remove(state->unacked_segs, curr_node);
  }
  /* Finally destroy the linked list itself */
  ll_destroy(state->unacked_segs);

  /* First clear the object from the list of segments to be sent and then remove the node itself */
  len = ll_length(state->segs_to_be_sent);
  for(i = 0; i < len; i++)
  {
    ll_node_t *curr_node = ll_front(state->segs_to_be_sent);
    free(curr_node->object);
    ll_remove(state->segs_to_be_sent, curr_node);
  }
  /* Finally destroy the linked list itself */
  ll_destroy(state->segs_to_be_sent);

  free(state);
  end_client();
}

/**
 * In this function we will send all the packets which are pending and are waiting to be sent in the sliding window.
 * @param state the current state of the ctcp protocol which has many parameters we will be using
 */
void clear_sliding_window(ctcp_state_t *state)
{
  int send_availbility = state->sliding_window_size - state->inflight_data ;
  int bdp = (state->bbr->rt_prop)* state->bbr->btlbw * 2.89; /* multiply by 2/ln2 */
  /* this means that there are more packets than required in the network */
  if(state->inflight_data >= bdp && bdp!=0)
  {
    return;
  }
  /* application is in the rate limited state so we only need to keep the total number of packets which are in flight at that time */
  if((ll_length(state->segs_to_be_sent)== 0) && (send_availbility ==0))
  {
    state->app_limited_until = state->inflight_data;
    return;
  }
  /* we will now drain the sliding window */
  ll_node_t *curr = state->unacked_segs->head;
  /* iterating through the segments */
  while(curr!=NULL)
  {
    void **node_buf = curr->object;
    ctcp_segment_t *seg = (ctcp_segment_t*)node_buf[0];
    if(*((int *)(node_buf[2])) == 0)
    {
      *((long *)(node_buf[1])) = state->last_packet_send_time = current_time();
      if(  state->app_limited_until >0   )
        *((int *)(node_buf[4])) = 1;
      usleep(2000);
      while(1)
      {
        /* if it is time to send the next packet */
        if(current_time() >= state->bbr->next_packet_send_time)
        {
          state->bbr->probe_bw_data = state->bbr->probe_bw_data + ntohs(seg->len);
          conn_send(state->conn,seg,ntohs(seg->len));
          break;
        }
      }
      *((int *)(node_buf[2])) = 1;
      /* the new packet sent will be added to the inflight data */
      state->inflight_data = state->inflight_data + ntohs(seg->len);
      if((state->max_seen_bw != -1) && (state->max_seen_bw !=0))
      {
          long len = ntohs(seg->len);
          long btl = state->bbr->btlbw;
          double pac = state->bbr->curr_pacing_gain;
          state->bbr->next_packet_send_time = current_time() + len /(pac  * btl);
      }
      else
      {
          state->bbr->next_packet_send_time = 0;
      }
    }
    curr = curr->next;
  }
}

/**
 * This will check the contents of the segments to be sent list and see if they are a part of the sliding window and remove the ones which are not necessary 
 * @param state the current state of the ctcp protocol which has many parameters we will be using
 */
void check_sliding_window(ctcp_state_t *state)
{
  /* if there are segments to be sent then iterate the list and check if we have already sent anything */
  if(ll_length(state->segs_to_be_sent)>0)
  {
    /* if the segment to be sent is within the sliding window */
    while((state->sliding_window_size < state->send_window) &&(ll_length(state->segs_to_be_sent)>0))
    {
      /* add to the front of the linked list */
      ll_add(state->unacked_segs,ll_front(state->segs_to_be_sent)->object);
      void **node_buf = (ll_front(state->segs_to_be_sent)->object);
      ctcp_segment_t *seg = (ctcp_segment_t*)node_buf[0];
      state->sliding_window_size = state->sliding_window_size + ntohs(seg->len);
      ll_remove(state->segs_to_be_sent,ll_front(state->segs_to_be_sent));
    }
  }
}

/**
 * This will prepare the segment which needs to be sent out of the link
 * @param state     the current state of the ctcp protocol which has many parameters we will be using
 * @param data      the data to be sent
 * @param data_size the total size of the data to be sent
 * @param flags     the required flags such as ACK or FIN
 * @param req_ack   this says whether ack is required to be sent or not
 */
void push_segment(ctcp_state_t *state,char *data,int data_size,int flags,int req_ack)
{
  ctcp_segment_t *send_segment = (ctcp_segment_t*)calloc(sizeof(ctcp_segment_t) + data_size,1);
  int send_segment_size = sizeof(ctcp_segment_t) + data_size;
  memcpy(send_segment->data,data,data_size);
  if(flags!=-1)
    send_segment->flags = flags;

  /* setting up the necessary field for sending the segment */
  send_segment->seqno = htonl(state->curr_seqno);
  send_segment->ackno = htonl(state->curr_ackno);
  send_segment->len = htons(send_segment_size);
  send_segment->window= htons(state->send_window);
  send_segment->cksum = 0;
  send_segment->cksum = cksum(send_segment,send_segment_size);
  if(req_ack == 1)
  {
    void **arr = malloc(5 * sizeof(void *));
    arr[0] = malloc(sizeof(ctcp_segment_t*));
    arr[1] = malloc(sizeof(long));
    arr[2] = malloc(sizeof(int));
    arr[3] = malloc(sizeof(int));
    arr[4] = malloc(sizeof(int));
    arr[0] = send_segment;
    *((int *)(arr[2])) = 0;
    *((int *)(arr[3])) = 0;
    *((int *)(arr[4])) = 0;

    /* add packet to send queue */
    ll_add(state->segs_to_be_sent,arr);
    state->curr_seqno = state->curr_seqno + data_size;

    /* check and clear the segment in the sliding window */
    check_sliding_window(state);
    clear_sliding_window(state);
  }
  else
  {
    usleep(2500);
    /* send the segment by calling conn_send */
    conn_send(state->conn,send_segment,send_segment_size);
    state->curr_seqno = state->curr_seqno + data_size;
    free(send_segment);
    free(data);   
  }  
}
void ctcp_read(ctcp_state_t *state) {
  /* FIXME */
  char stdin_data[MAX_SEG_DATA_SIZE];
  int stdin_read_size;
  int chunk_iter;
  while(1)
  {
    /* read data from stdin */
    stdin_read_size = conn_input(state->conn,stdin_data,MAX_SEG_DATA_SIZE);

    /* if read data size is greater than the send window size */
    if(stdin_read_size > state->send_window)
    {
      int stdin_chunk_size =state->send_window;
      for(chunk_iter=0;chunk_iter<stdin_read_size;chunk_iter=chunk_iter+stdin_chunk_size)
      {
        /* transmit the segment on the output */
        push_segment(state,stdin_data+chunk_iter,stdin_chunk_size,TH_ACK,1);           
      }
    }
    /* if the data to be read returns -1 then it is a FIN segment */
    if ((stdin_read_size == -1))
    {
    
      if(ll_length(state->segs_to_be_sent)!=0)
        return;
      if(state->conn_state ==0)
      {
      push_segment(state,NULL,0,TH_FIN,0);
      }
      else
      {
        push_segment(state,NULL,0,TH_FIN,0);
        ctcp_destroy(state);
      }
      return;
    }
    /* if conn_input returns 0 then we just need to exit from loop and no need to do anything */
    if(stdin_read_size == 0)
      break;
    push_segment(state,stdin_data,stdin_read_size,TH_ACK,1);
  }
  return;
}

void ctcp_receive(ctcp_state_t *state, ctcp_segment_t *segment, size_t len) {
  int dup_flag =0;
  int csum = segment->cksum;
  segment->cksum =0;
  /* checking if packet checksum matches */
  if(csum != cksum(segment,sizeof(ctcp_segment_t)+strlen(segment->data)))
  {
    free(segment);
    return;
  }

  /* if the packet is a FIN segment send an ACK for the FIN */
  if(((segment->flags & TH_FIN) == TH_FIN) &&(state->conn_state ==0))
  {
    state->conn_state = 1;
    push_segment(state,NULL,0,TH_ACK,0);
    conn_output(state->conn,segment->data,0);
    return;
  }
  else if(state->conn_state == 1)
  {
    ctcp_destroy(state);
    return;
  }

  /* if the segment is an ACK segment */
  if((segment->flags & TH_ACK) == TH_ACK)
  {
    if(state->conn_state == 1)
      ctcp_destroy(state);

    /* while there are still segments which are unacknowledged */
    while(ll_length(state->unacked_segs)>0)
    {
      ll_node_t *seg_node = ll_front(state->unacked_segs);
      void **node_buf = (seg_node->object);
      ctcp_segment_t *seg = (ctcp_segment_t*)node_buf[0];

      /* if the seqno is less than the ackno means that we can send some segments */
      if(ntohl(seg->seqno) < ntohl(segment->ackno))
      {
        /* calculate RTT here */
        long rtt = current_time() - *((long*)node_buf[1]);

        /* track the rtt samples and also the rt_prop */
        if(state->min_seen_rtt ==-1)
        {
          state->min_seen_rtt = rtt;
          state->bbr->rt_prop = rtt;
        }
        else
        {
          /* if the rtt value calculated is the minimum seen RTT then update the value of the min RTT */
          if(rtt <= state->min_seen_rtt)
          {
            state->min_seen_rtt = rtt;
            /* if we are i nthe PROBE_BW mode we need to move to the STARTUP mode */
            if(state->bbr->mode == BBR_PROBE_BW)
              state->bbr->mode = BBR_STARTUP;
          }
        }
        if(state->bbr->num_rtt == 0)
        {
          state->max_seen_bw = state->bbr->btlbw = change_to_probe_bw(state->bbr,rtt,ntohs(seg->len));  
        }
        else
        {
          if(*((long*)node_buf[4]) ==0)
          {
            static int cnt;
            cnt++;
            if(cnt == 1)
              fprintf(state->bdp_fptr,"%ld,%d\n",current_time(),0);
            float bw = change_to_probe_bw(state->bbr,rtt,ntohs(seg->len));
            /* store the current time and the BDP in the file */
            fprintf(state->bdp_fptr,"%ld,%ld\n",current_time(),state->bbr->btlbw*rtt*8);
            fflush(state->bdp_fptr);

            /* update the values of the startup bandwidth here */
            if(state->max_seen_bw < bw)
            {
              state->max_seen_bw = bw;
              state->bbr->latest_bw = state->bbr->prev_bw;
              state->bbr->prev_bw = state->bbr->oldest_bw;
              state->bbr->oldest_bw = bw;
            }
            /* if the number of RTTs have been same for 10 seconds */
            if(state->bbr->num_rtt % 10 == 0)
            {
              if(state->bbr->btlbw < state->max_seen_bw)
                state->bbr->btlbw = state->max_seen_bw;
            }
          }
        }
        /* check the bbr state as we have received an ACK */
        check_bbr_state(state->bbr);
        state->send_window =state->cfg->send_window= state->bbr->cwnd;
        if(state->app_limited_until > 0)
          state->app_limited_until = state->app_limited_until - ntohs(seg->len);
        state->sliding_window_size = state->sliding_window_size - ntohs(seg->len);
        state->inflight_data = state->inflight_data - ntohs(seg->len);
        free(ll_remove(state->unacked_segs,seg_node));
        /* if there are still some segments which can be sent */
        if(ll_length(state->segs_to_be_sent) > 0)
        {
          check_sliding_window(state);
          clear_sliding_window(state);
        }
      }
      else /* break if we need not send new segments */
      {
        break;
      }
    }
  }
  int data_len = ntohs(segment->len)-sizeof(ctcp_segment_t);
  state->curr_ackno = ntohl(segment->seqno) + data_len;
  if(ntohl(segment->seqno) <   state->prev_ackno)
  {
    dup_flag = 1;
  }
  else
  {
    state->prev_ackno = state->curr_ackno;
    dup_flag =0;
  }
  char *segment_data_buf = (char*)malloc(sizeof(char)*MAX_SEG_DATA_SIZE);
  strncpy(segment_data_buf,segment->data,data_len);

  /* send ack */
  if(ntohs(segment->len) > sizeof(ctcp_segment_t))
  { 
    if(dup_flag ==0)
    {
      push_segment(state,NULL,0,TH_ACK,0);
      conn_output(state->conn,segment_data_buf,data_len);
    }
  }
   free(segment);
   free(segment_data_buf);
}

void ctcp_output(ctcp_state_t *state) {
  //int bytes_avail = conn_bufspace(state->conn);
  /* FIXME */
  push_segment(state,NULL,0,TH_ACK,0);
}

void ctcp_timer() {
  /* FIXME */
  ctcp_state_t *curr_state = state_list;
  while (curr_state!= NULL) {

    /* keep a track of when the timer was last called */
    if(curr_state->last_timer_call ==0)
      curr_state->last_timer_call = current_time();

    /* this the window of RTT is greater than the 0 */
    if(curr_state->bbr->rtt_m_win > 0) 
    {
      curr_state->bbr->rtt_m_win = curr_state->bbr->rtt_m_win - (current_time() - curr_state->last_timer_call);
      curr_state->last_timer_call = current_time();
    }

    /* if the window if RTT is less then 0 we have reached the end of 1 RTT measurement period */
    if(curr_state->bbr->rtt_m_win <0)
    {
      /* we need to go into PROBE_RTT mode */
      change_to_probe_rtt(curr_state->bbr,curr_state->min_seen_rtt);
      if(curr_state->min_seen_rtt < curr_state->bbr->rt_prop)
      {
        curr_state->bbr->rt_prop = curr_state->min_seen_rtt;
        curr_state->bbr->rtt_change_timestamp = current_time();
      }
      /* reset the window timer */
      curr_state->bbr->rtt_m_win = 10000;
    }

    ll_node_t *curr_segment = curr_state->unacked_segs->head;
    while(curr_segment!=NULL)
    {
      void **node_buf = (curr_segment->object);
      if((( current_time() - *((long*)node_buf[1]) > curr_state->cfg->rt_timeout)&& (*((int*)node_buf[3])<5)))
      {
        ctcp_segment_t *seg = (ctcp_segment_t*)node_buf[0];
        *((long*)node_buf[1]) = current_time();
        curr_state->sliding_window_size = curr_state->sliding_window_size - ntohs(seg->len);
        *((int*)node_buf[3]) = *((int*)node_buf[3]) + 1;
        conn_send(curr_state->conn,seg,ntohs(seg->len));
      }
      /* if there are more than 5 retransmissions */
      if(*((int*)node_buf[3]) >=5)
      {
        ctcp_destroy(curr_state);
        break;
      }
      curr_segment=curr_segment->next;
    }
    curr_state=curr_state->next;
  }
}