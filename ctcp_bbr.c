#include "ctcp_bbr.h"

struct bbr* bbr_init()
{
	/* allocate memory for the structure */
	struct bbr *bbr = malloc(sizeof(struct bbr));

	/* the initial number of rtts is 0 */
	bbr->num_rtt = 0;

	/* start in the BBR_STARTUP mode */
	bbr->mode = 1;

	/* we check the min rtt for 10 seconds */ 
	bbr->min_rtt_win_sec = 10;

	/* this is the min_rtt_win_sec in msec */
	bbr->rtt_m_win = 10000;

	/* set the bottleneck bandwidth to the max data that can be sent on the link */
	bbr->btlbw = 1440;

	/* start from the first value of pacing gain */
	bbr->curr_pacing_gain = bbr_pacing_gain[0];

	/* next send packet time will also be 0 */
	bbr->next_packet_send_time = 0;

	/* set this as the initial value rt prop */
	bbr->rt_prop = 200;

	return bbr;
}

void change_to_probe_rtt(struct bbr *bbr,long rtt)
{
	/* if the current mode is BBR_PROBE_RTT */
	if( bbr->mode  == BBR_PROBE_RTT)
		bbr->rt_prop = rtt;

	/* need to change to PROBE_RTT since we have been sending at rt_prop for 10 secs */
	if((bbr->rt_prop <= current_time() - bbr->rtt_change_timestamp))
	{
		/* change from PROBE_BW to PROBE_BW */
		if(bbr->mode == BBR_PROBE_BW)
		{
			bbr->probe_rtt_start_round = bbr->num_rtt;
			bbr->mode = BBR_PROBE_RTT;
		}
	}

	/* need to change to PROBE_RTT since rtt has decreased */
	if(rtt < bbr->rt_prop)
	{
		bbr->rt_prop = rtt;
		/* change from PROBE_RTT to STARTUP */
		if(bbr->mode == BBR_PROBE_RTT)
			bbr->mode = BBR_STARTUP;

		/* change from PROBE_BW to PROBE_RTT */
		if(bbr->mode == BBR_PROBE_BW)
		{
			bbr->probe_rtt_start_round = bbr->num_rtt;
			bbr->mode = BBR_PROBE_RTT;
		}
	}
}

float change_to_probe_bw(struct bbr *bbr,long rtt,int segment_length)
{
	bbr->num_rtt = bbr->num_rtt + 1;
	if(rtt)
	{
		/* calculate the bandwidth of bbr */
		float bw = (float)segment_length/(float)(rtt);
		return bw;
	}
	return 0.0;
}

void check_bbr_state(struct bbr *bbr)
{
	/* if the current mode is STARTUP */
	if (bbr->mode == BBR_STARTUP)
	{
		/* if the number of rtts is a multiple of 3 and the third oldest value of bw exists */
		if((bbr->num_rtt % 3 == 0) && (bbr->oldest_bw != 0))
		{
			/* change to DRAIN mode */
			if(( (bbr->latest_bw - bbr->oldest_bw)/bbr->oldest_bw *100) >25)
			{
				bbr->mode = BBR_DRAIN;
				bbr->drain_start_round = bbr->num_rtt;
				return;
			}
		}
		/* change to drain mdoe */
		if((bbr->num_rtt % 3 == 0) && (bbr->oldest_bw == 0))
		{
			bbr->mode = BBR_DRAIN;
			bbr->drain_start_round = bbr->num_rtt;
			return;
		}
		else
		{
			bbr->curr_pacing_gain = bbr_pacing_gain[1];
		}

		/* pacing the congestion window */
		bbr->cwnd =(float) (bbr->rt_prop) * bbr->btlbw *bbr_pacing_gain[1];
		return ;
	}
	else if (bbr->mode == BBR_DRAIN) /* if the current mode is DRAIN */
	{
		/* if the number of packets inflight is less then BDP then go to PROBE_BW mode  */
		if( bbr->num_rtt - bbr->drain_start_round >=10)
		{
			bbr->probe_bw_phase =2;
			bbr->probe_bw_data =0;
			bbr->mode = BBR_PROBE_BW;
			return;
		}
		bbr->curr_pacing_gain = bbr_pacing_gain[2];
		bbr->cwnd =  bbr->rt_prop * bbr->btlbw * bbr_pacing_gain[2];
		return;
	}
	else if (bbr->mode == BBR_PROBE_BW) /* if the current mode is PROBE_BW */
	{
		if(bbr->probe_bw_data >= (bbr->btlbw *bbr->rt_prop/1000))
		{
			if(bbr->probe_bw_phase == 8)
				bbr->probe_bw_phase = 1;
			bbr->probe_bw_phase = bbr->probe_bw_phase +1;
			bbr->probe_bw_data = 0;
		}
		bbr->cwnd =  bbr->rt_prop * bbr->btlbw * bbr_pacing_gain[5];
		bbr->curr_pacing_gain = bbr_pacing_gain[bbr->probe_bw_phase];
		return;
	}
	else if (bbr->mode == BBR_PROBE_RTT) /* if the current mode is PROBE_RTT */
	{
		if(bbr->num_rtt - bbr->probe_rtt_start_round >=4)
		{
			bbr->probe_bw_phase =2;
			bbr->probe_bw_data =0;
			bbr->mode = BBR_PROBE_BW;
		}
		bbr->curr_pacing_gain = bbr_pacing_gain[5];
		bbr->cwnd = bbr->rt_prop * bbr->btlbw * bbr_pacing_gain[1];
		return;
	}
}