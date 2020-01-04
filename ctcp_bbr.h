#include <stdio.h>
#include <unistd.h>
#include <stdint.h>
#include "ctcp_sys.h"
#include "ctcp_utils.h"

struct bbr
{
	uint32_t num_rtt; /* keep count of the number of rtt's elapsed, if >=10 update the bbr bw*/
	uint32_t mode; /* current bbr mode */
	uint32_t min_rtt_stamp; /* timestamp of min_rtt_us */
	uint32_t min_rtt_us; /* minimum rtt in min_rtt_win_sec window */
	uint32_t min_rtt_win_sec; /* the minimum rtt for a specific window size */
	uint32_t probe_rtt_done_stamp; /* this will end the BBR_PROBE_RTT mode */
	uint32_t cwnd; /* this is the congestion window for bbr which will be initialized to the sender window size */

	/* in startup mode we need to find best of 3 non rate limited */
	uint32_t oldest_bw; /* oldest startup bw size */
	uint32_t prev_bw; /* second oldest startup bw size */
	uint32_t latest_bw; /* most recent startup bw size */

	uint32_t btlbw; /* every 10 rtt the updated bottleneck bandwidth will be stored */
	uint32_t probe_bw_data; /* data sent in BBR_PROBE_BW mode */
	uint32_t probe_bw_phase; /* phase in probe bw */

	int32_t rtt_m_win; /* total time for which min RTT must be considered and is expresses in ms */
	int32_t	rt_prop; /* round trip propagation value of bbr */

	float curr_pacing_gain; /* tracks the current pacing gain value */
	long next_packet_send_time;	/* next packet send time*/	
	long rtt_change_timestamp; /* indicates when the rt_prop has been updated*/
	int drain_start_round; /* indicates start of drain phase */
	int probe_rtt_start_round; /* beginning of PROBE_RTT round */
};

enum bbr_mode {
	BBR_STARTUP, /* ramp up sending rate rapidly to fill pipe */
	BBR_DRAIN, /* drain any queue created during startup */
	BBR_PROBE_BW, /* discover, share bw: pace around estimated bw */
	BBR_PROBE_RTT, /* cut cwnd to min to probe min_rtt */
};

static const float bbr_pacing_gain[] = {2.89, 1/2.89, 5/4, 3/4, 1, 1, 1, 1, 1, 1};

struct bbr* bbr_init();
void change_to_probe_rtt(struct bbr *bbr,long rtt);
float change_to_probe_bw(struct bbr *bbr,long rtt,int segment_length);
void check_bbr_state(struct bbr *bbr);