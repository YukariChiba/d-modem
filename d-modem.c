/* 
 * Copyright (C) 2021 Aon plc
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA 
 */

#include <fcntl.h>
#include <unistd.h>
#include <stdbool.h>
#include <time.h>
#include <stdio.h>
#include <signal.h>
#include <errno.h>
// test
#include <pjsua-lib/pjsua.h>
#include <pjsua-lib/pjsua_internal.h>

#ifdef HAS_PULSE
#include <pulse/simple.h>
#endif

#define PCM_FILE "dmodem.%s.s16le.9600hz.pcm"
#undef PCM_FILE
//#define HAS_PULSE

#ifdef HAS_PULSE
pa_simple *pa_s1 = NULL;
pa_simple *pa_s2 = NULL;
const pa_sample_spec pa_ss = {
	.format = PA_SAMPLE_S16LE,
	.rate = 9600,
	.channels = 1
};
#endif
#ifdef PCM_FILE
int wave_recv = -1;
int wave_transmit = -1;
#endif

#define SIGNATURE PJMEDIA_SIG_CLASS_PORT_AUD('D','M')
#define DMODEM_DIAL_MODE 0
#define DMODEM_ANSWER_MODE 1

uint8_t mode = DMODEM_DIAL_MODE;
uint8_t ringing = 0;
uint8_t answered = 0;
int ppid;
pjsua_call_id incoming;

struct dmodem {
	pjmedia_port base;
	pj_timestamp timestamp;
	pj_sock_t sock;
};

static struct dmodem port;
static bool destroying = false;
static pj_pool_t *pool;

void stop_pa();
static void error_exit(const char *title, pj_status_t status) {
	pjsua_perror(__FILE__, title, status);
	if (!destroying) {
		destroying = true;
		pjsua_destroy();
		stop_pa();
		if (answered)
			kill(ppid, SIGINT);
		exit(1);
	}
}

void start_pa() {
#ifdef PCM_FILE
	char _wavbuf[50];
	sprintf(_wavbuf, PCM_FILE, "recv");
	wave_recv = open(_wavbuf, O_WRONLY | O_CREAT, 00644);
	sprintf(_wavbuf, PCM_FILE, "transmit");
	wave_recv = open(_wavbuf, O_WRONLY | O_CREAT, 00644);
	if (wave_recv <= 0 || wave_transmit <= 0)
		error_exit("open wave files", 1);
#endif
#ifdef HAS_PULSE
	pa_s1 = pa_simple_new(NULL,               // Use the default server.
						"dmodem",           // Our application's name.
						PA_STREAM_PLAYBACK,
						NULL,               // Use the default device.
						"recv",            // Description of our stream.
						&pa_ss,                // Our sample format.
						NULL,               // Use default channel map
						NULL,               // Use default buffering attributes.
						NULL               // Ignore error code.
						);
	pa_s2 = pa_simple_new(NULL,               // Use the default server.
						"dmodem",           // Our application's name.
						PA_STREAM_PLAYBACK,
						NULL,               // Use the default device.
						"transmit",            // Description of our stream.
						&pa_ss,                // Our sample format.
						NULL,               // Use default channel map
						NULL,               // Use default buffering attributes.
						NULL               // Ignore error code.
						);
	if (!pa_s1 || !pa_s2)
		error_exit("pulseaudio", 1);
#endif
}

void stop_pa() {
#ifdef HAS_PULSE
	if(pa_s1)
		pa_simple_free(pa_s1);
	if(pa_s2)
		pa_simple_free(pa_s2);
	pa_s1 = 0;
	pa_s1 = 0;
#endif
#ifdef PCM_FILE
	if (wave_recv >= 0)
		close(wave_recv);
	if (wave_transmit >= 0)
		close(wave_transmit);
	wave_recv = -1;
	wave_transmit = -1;
#endif
}

static pj_status_t dmodem_put_frame(pjmedia_port *this_port, pjmedia_frame *frame) {
	struct dmodem *sm = (struct dmodem *)this_port;
	int len;

	if (frame->type == PJMEDIA_FRAME_TYPE_AUDIO) {
#ifdef HAS_PULSE
		pa_simple_write(pa_s1, frame->buf, frame->size, NULL);
#endif
#ifdef PCM_FILE
		write(wave_recv, frame->buf, frame->size);
#endif
		if ((len=write(sm->sock, frame->buf, frame->size)) != frame->size) {
			error_exit("error writing frame",0);
		}
	}

	return PJ_SUCCESS;
}

static pj_status_t dmodem_get_frame(pjmedia_port *this_port, pjmedia_frame *frame) {
	struct dmodem *sm = (struct dmodem *)this_port;
	frame->size = PJMEDIA_PIA_AVG_FSZ(&this_port->info); // MAX? what is

	int len;
	if ((len=read(sm->sock, frame->buf, frame->size)) != frame->size) {
		error_exit("error reading frame",0);
	}

	frame->timestamp.u64 = sm->timestamp.u64;
	frame->type = PJMEDIA_FRAME_TYPE_AUDIO;
	sm->timestamp.u64 += PJMEDIA_PIA_SPF(&this_port->info);
#ifdef HAS_PULSE
	pa_simple_write(pa_s2, frame->buf, frame->size, NULL);
#endif
#ifdef PCM_FILE
	write(wave_transmit, frame->buf, frame->size);
#endif

	return PJ_SUCCESS;
}

static pj_status_t dmodem_on_destroy(pjmedia_port *this_port) {
	printf("destroy\n");
	if (answered)
		kill(ppid, SIGINT);
	exit(-1);
}

/* Callback called by the library when call's state has changed */
static void on_call_state(pjsua_call_id call_id, pjsip_event *e) {
	pjsua_call_info ci;

	PJ_UNUSED_ARG(e);

	pjsua_call_get_info(call_id, &ci);
	PJ_LOG(3,(__FILE__, "Call %d state=%.*s", call_id,
				(int)ci.state_text.slen,
				ci.state_text.ptr));

	if (ci.state == PJSIP_INV_STATE_DISCONNECTED) {
		close(port.sock);
		if (!destroying) {
			destroying = true;
			pjsua_destroy();
			stop_pa();
			if (answered)
				kill(ppid, SIGINT);
			exit(0);
		}
	}
}

static void on_incoming_call(pjsua_acc_id acc_id, pjsua_call_id call_id, pjsip_rx_data *rdata) {
	pjsua_call_info ci;
	pjsua_call_get_info(call_id, &ci);
	char* tmp = malloc(pj_strlen(&ci.remote_contact)+1);
	strcpy(tmp, ci.remote_contact.ptr);
	tmp[pj_strlen(&ci.remote_contact)] = '\0';
	if (answered) {
		PJ_LOG(2,(__FILE__, "Incoming call rejected from: %s", tmp));
		pjsua_call_hangup(call_id, 0u, NULL, NULL);
		return;
	}
	PJ_LOG(2,(__FILE__, "Incoming call from: %s", tmp));
	free(tmp);
	incoming = call_id;
	ringing = 1;
}

/* Callback called by the library when call's media state has changed */
static void on_call_media_state(pjsua_call_id call_id) {
	pjsua_call_info ci;
	pjsua_conf_port_id port_id;
	static int done=0;

	pjsua_call_get_info(call_id, &ci);

//	printf("media_status %d media_cnt %d ci.conf_slot %d aud.conf_slot %d\n",ci.media_status,ci.media_cnt,ci.conf_slot,ci.media[0].stream.aud.conf_slot);
	if (ci.media_status == PJSUA_CALL_MEDIA_ACTIVE) {
		if (!done) {
			pjsua_conf_add_port(pool, &port.base, &port_id);
			pjsua_conf_connect(ci.conf_slot, port_id);
			pjsua_conf_connect(port_id, ci.conf_slot);
			done = 1;
		}
	} else {
		done = 0;
	}
}

int main(int argc, char *argv[]) {
	pjsua_acc_id acc_id;
	pj_status_t status;
	if (argc != 5) {
		return -1;
	}
	ppid = atoi(argv[3]);
	char* _modemid_tmp = argv[4];
	for (size_t i = strlen(_modemid_tmp) - 1 ; ; i--) {
		if (i < 0 || _modemid_tmp[i] < '0' || _modemid_tmp[i] > '9') {
			_modemid_tmp += i+1;
			break;
		}
	}
	int sip_port = atoi(_modemid_tmp);
	sip_port = sip_port >= 0 ? sip_port : 0;
	sip_port += 5060;
	sip_port = sip_port <= 65535 ? sip_port : 65535;
	if(!strncmp(argv[1], "rr", 2)) {
		mode = DMODEM_ANSWER_MODE;
	}
	signal(SIGPIPE,SIG_IGN);
	char *dialstr = argv[1];

	int has_sip_user = 1;
	const char *_sip_user = getenv("SIP_LOGIN");
	char *sip_user = NULL;
	if (!_sip_user) {
		has_sip_user = 0;
		_sip_user = "placeholder:placeholder@placeholder";
	}
	sip_user = malloc(strlen(_sip_user) + 1);
	strcpy(sip_user, _sip_user);
	char *sip_domain = strchr(sip_user,'@');
	if (!sip_domain) {
		return -1;
	}
	*sip_domain++ = '\0';
	char *sip_pass = strchr(sip_user,':');
	if (!sip_pass) {
		return -1;
	}
	*sip_pass++ = '\0';

	status = pjsua_create();
	if (status != PJ_SUCCESS) error_exit("Error in pjsua_create()", status);
	if (!has_sip_user)
		PJ_LOG(2,(__FILE__, "SIP_LOGIN is empty, no registration will be attempted"));

	/* Init pjsua */
	{
		pjsua_config cfg;
		pjsua_logging_config log_cfg;
		pjsua_media_config med_cfg;

		pjsua_config_default(&cfg);
		cfg.cb.on_call_media_state = &on_call_media_state;
		cfg.cb.on_call_state = &on_call_state;

		if(mode == DMODEM_ANSWER_MODE) {
			cfg.cb.on_incoming_call = &on_incoming_call;
		}

		pjsua_logging_config_default(&log_cfg);
//		log_cfg.console_level = 4;
		log_cfg.console_level = 2;

		pjsua_media_config_default(&med_cfg);
		med_cfg.no_vad = true;
		med_cfg.ec_tail_len = 0;
		med_cfg.jb_max = -1;
		med_cfg.jb_init = -1;
		med_cfg.audio_frame_ptime = 10;
		med_cfg.quality = 10;
		med_cfg.enable_ice = PJ_FALSE;
		med_cfg.enable_turn = PJ_FALSE;

		status = pjsua_init(&cfg, &log_cfg, &med_cfg);
		if (status != PJ_SUCCESS) error_exit("Error in pjsua_init()", status);
	}

	pjsua_set_ec(0,0); // maybe?
	pjsua_set_null_snd_dev();

	/* g711 only */
	pjsua_codec_info codecs[32];
	unsigned count = sizeof(codecs)/sizeof(*codecs);
	pjsua_enum_codecs(codecs,&count);
	for (int i=0; i<count; i++) {
		int pri = 0;
		if (pj_strcmp2(&codecs[i].codec_id,"PCMA/8000/1") == 0) {
			pri = 1;
		} else if (pj_strcmp2(&codecs[i].codec_id,"PCMU/8000/1") == 0) {
			pri = 2;
		}
		pjsua_codec_set_priority(&codecs[i].codec_id, pri);
//		printf("codec: %s %d\n",pj_strbuf(&codecs[i].codec_id),pri);
	}

	pjsua_transport_id transport_id;
	/* Add UDP transport. */
	{
		pjsua_transport_config cfg;
		int max_port = 65535;

		pjsua_transport_config_default(&cfg);

		while (sip_port <= max_port) {
			if (mode)
				cfg.port = sip_port;
			if (getenv("PJSIP_IPV6"))
				status = pjsua_transport_create(PJSIP_TRANSPORT_UDP6, &cfg, &transport_id);
			else
				status = pjsua_transport_create(PJSIP_TRANSPORT_UDP, &cfg, &transport_id);

			if (status == PJ_SUCCESS) {
				if (mode)
					PJ_LOG(2,(__FILE__, "SIP transport created on port %d", sip_port));
				break;
			}

			/* Port is in use, try next port */
			if (mode) {
				PJ_LOG(2,(__FILE__, "Port %d is in use, trying %d", sip_port, sip_port + 1));
				sip_port++;
			} else {
				/* In dial mode with no specific port, just fail */
				error_exit("Error creating transport", status);
			}
		}

		if (sip_port > max_port) {
			error_exit("Could not find an available SIP port", PJ_EINVAL);
		}
	}
	char buf[384];
	//printf("Initializing pool\n");
	pj_caching_pool cp;
	pj_caching_pool_init(&cp, NULL, 1024*1024);
	pool = pj_pool_create(&cp.factory, "pool1", 4000, 4000, NULL);

	pj_str_t name = pj_str("dmodem");

	memset(&port,0,sizeof(port));
	port.sock = atoi(argv[2]); // inherited from parent
	pjmedia_port_info_init(&port.base.info, &name, SIGNATURE, 9600, 1, 16, 192);
	port.base.put_frame = dmodem_put_frame;
	port.base.get_frame = dmodem_get_frame;
	port.base.on_destroy = dmodem_on_destroy;

	memset(buf,0,sizeof(buf));
	write(port.sock, buf, sizeof(buf));
	/* Initialization is done, now start pjsua */
	status = pjsua_start();
	if (status != PJ_SUCCESS) error_exit("Error starting pjsua", status);

	if (has_sip_user)
	{
		pjsua_acc_config cfg;
		pjsua_acc_config_default(&cfg);
		snprintf(buf,sizeof(buf),"sip:%s@%s",sip_user,sip_domain);
		pj_strdup2(pool,&cfg.id,buf);
		snprintf(buf,sizeof(buf),"sip:%s",sip_domain);
		pj_strdup2(pool,&cfg.reg_uri,buf);
		cfg.register_on_acc_add = mode ? true : false;
		cfg.cred_count = 1;
		cfg.cred_info[0].realm = pj_str("*");
		cfg.cred_info[0].scheme = pj_str("digest");
		cfg.cred_info[0].username = pj_str(sip_user);
		cfg.cred_info[0].data_type = PJSIP_CRED_DATA_PLAIN_PASSWD;
		cfg.cred_info[0].data = pj_str(sip_pass);

		status = pjsua_acc_add(&cfg, PJ_TRUE, &acc_id);
		if (status != PJ_SUCCESS) error_exit("Error adding account", status);
	}
	else
	{
		status == pjsua_acc_add_local(transport_id, 1, &acc_id);
		if (status != PJ_SUCCESS) error_exit("Error adding local account", status);
	}
	pjsua_get_var()->tpdata[transport_id].has_bound_addr = PJ_TRUE;
	if (getenv("PJSIP_IPV6"))
		pjsua_get_var()->acc[acc_id].cfg.ipv6_media_use = PJSUA_IPV6_ENABLED;

	start_pa();
	if(mode == DMODEM_DIAL_MODE) {
		if (has_sip_user)
			snprintf(buf,sizeof(buf),"sip:%s@%s",dialstr,sip_domain);
		else
			snprintf(buf,sizeof(buf),"sip:%s",dialstr);
		PJ_LOG(2,(__FILE__, "calling %s\n",buf));
		pj_str_t uri = pj_str(buf);
		pjsua_call_id callid;
		status = pjsua_call_make_call(acc_id, &uri, 0, NULL, NULL, &callid);
		if (status != PJ_SUCCESS) error_exit("Error making call", status);
	}

	struct timespec ts = {1, 0};
	while(1) {
		if(mode == DMODEM_ANSWER_MODE) {
			if(ringing) {
				status = pjsua_call_answer(incoming, 200, NULL, NULL);
				if (status != PJ_SUCCESS) error_exit("Error answering call", status);
				ringing = 0;
				answered = 1;
			}
		}
		nanosleep(&ts,NULL);
	}

	return 0;
}
