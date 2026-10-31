#include "mospf_daemon.h"
#include "mospf_proto.h"
#include "mospf_nbr.h"
#include "mospf_database.h"

#include "ip.h"
#include "rtable.h"
#include "arp.h"

#include "list.h"
#include "log.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <limits.h>

extern ustack_t *instance;

pthread_mutex_t mospf_lock;

static u8 mospf_multicast_mac[ETH_ALEN] = {0x01, 0x00, 0x5e, 0x00, 0x00, 0x05};	// 224.0.0.5

// 下面两个函数在文件里“先被调用、后被定义”，需要前置声明
static void send_mospf_lsu(void);
void mospf_update_rtable(void);

void mospf_init()
{
	pthread_mutex_init(&mospf_lock, NULL);

	instance->area_id = 0;
	// get the ip address of the first interface
	iface_info_t *iface = list_entry(instance->iface_list.next, iface_info_t, list);
	instance->router_id = iface->ip;
	instance->sequence_num = 0;
	instance->lsuint = MOSPF_DEFAULT_LSUINT;

	iface = NULL;
	list_for_each_entry(iface, &instance->iface_list, list) {
		iface->helloint = MOSPF_DEFAULT_HELLOINT;
		init_list_head(&iface->nbr_list);
	}

	init_mospf_db();
}

void *sending_mospf_hello_thread(void *param);
void *sending_mospf_lsu_thread(void *param);
void *checking_nbr_thread(void *param);
void *checking_database_thread(void *param);

void mospf_run()
{
	pthread_t hello, lsu, nbr, db;
	pthread_create(&hello, NULL, sending_mospf_hello_thread, NULL);
	pthread_create(&lsu, NULL, sending_mospf_lsu_thread, NULL);
	pthread_create(&nbr, NULL, checking_nbr_thread, NULL);
	pthread_create(&db, NULL, checking_database_thread, NULL);
}

static void sending_mospf_hello(iface_info_t *iface)
{
	int len = ETHER_HDR_SIZE + IP_BASE_HDR_SIZE + MOSPF_HDR_SIZE + MOSPF_HELLO_SIZE;
	char *packet = (char *)malloc(len);
	memset(packet, 0, len);

	struct ether_header *eh = (struct ether_header *)packet;
	memcpy(eh->ether_dhost, mospf_multicast_mac, ETH_ALEN);
	memcpy(eh->ether_shost, iface->mac, ETH_ALEN);
	eh->ether_type = htons(ETH_P_IP);

	struct iphdr *ip = packet_to_ip_hdr(packet);
	ip_init_hdr(ip, iface->ip, MOSPF_ALLSPFRouters, IP_BASE_HDR_SIZE + MOSPF_HDR_SIZE + MOSPF_HELLO_SIZE, IPPROTO_MOSPF);

	struct mospf_hdr *mospf = (struct mospf_hdr *)IP_DATA(ip);
	mospf_init_hdr(mospf, MOSPF_TYPE_HELLO, MOSPF_HDR_SIZE + MOSPF_HELLO_SIZE, instance->router_id, instance->area_id);

	struct mospf_hello *hello = (struct mospf_hello *)((char *)mospf + MOSPF_HDR_SIZE);
	mospf_init_hello(hello, iface->mask);

	mospf->checksum = mospf_checksum(mospf);

	iface_send_packet(iface, packet, len);
} 

void *sending_mospf_hello_thread(void *param)
{
	while (1)
	{
		sleep(1);
		pthread_mutex_lock(&mospf_lock);

		iface_info_t *iface = NULL;
		list_for_each_entry(iface, &instance->iface_list, list) {
			iface->helloint -= 1;
			if (iface->helloint <= 0) {
				sending_mospf_hello(iface);
				iface->helloint = MOSPF_DEFAULT_HELLOINT;	// 5s
			}
		}

		pthread_mutex_unlock(&mospf_lock);
	}
}

static mospf_nbr_t *create_mospf_nbr(u32 rid, u32 ip, u32 mask, int helloint)
{
	mospf_nbr_t *nbr = (mospf_nbr_t *)malloc(sizeof(*nbr));
	memset(nbr, 0, sizeof(*nbr));

	init_list_head(&nbr->list);
	nbr->nbr_id   = rid;
	nbr->nbr_ip   = ip;
	nbr->nbr_mask = mask;
	nbr->alive    = 3 * helloint;

	return nbr;
}

static void update_nbr_list_via_hello(iface_info_t *iface, u32 rid, u32 nbr_ip, u32 mask, int helloint)
{
	mospf_nbr_t *nbr = NULL;

	list_for_each_entry(nbr, &iface->nbr_list, list) {
		if (nbr->nbr_id == rid && nbr->nbr_ip == nbr_ip) {
			nbr->alive = 3 * helloint;
			return;
		}
	}

	nbr = create_mospf_nbr(rid, nbr_ip, mask, helloint);
	list_add_tail(&nbr->list, &iface->nbr_list);
	iface->num_nbr += 1;

	instance->sequence_num += 1;
	send_mospf_lsu();
}

void *checking_nbr_thread(void *param)
{
	while (1) {
		sleep(1);

		int changed = 0;

		pthread_mutex_lock(&mospf_lock);
		iface_info_t *iface = NULL;
		list_for_each_entry(iface, &instance->iface_list, list) {
			mospf_nbr_t *nbr = NULL, *q = NULL;
			list_for_each_entry_safe(nbr, q, &iface->nbr_list, list) {
				if (nbr->alive == 0) {
					list_delete_entry(&nbr->list);
					free(nbr);
					changed = 1;
					iface->num_nbr -= 1;
				}
				else {
					nbr->alive -= 1;
				}
			}
		}
		pthread_mutex_unlock(&mospf_lock);

		if (changed) {
			instance->sequence_num += 1;	// new lsu msg
			send_mospf_lsu();
		}
	}

	return NULL;
}

void *checking_database_thread(void *param)
{
	while (1)
	{
		sleep(1);

		int changed = 0;

		pthread_mutex_lock(&mospf_lock);

		mospf_db_entry_t *entry = NULL, *q = NULL;
		list_for_each_entry_safe(entry, q, &mospf_db, list) {
			entry->alive -= 1;
			if (entry->alive <= 0) {
				list_delete_entry(&entry->list);
				free(entry->array);
				free(entry);
				changed = 1;
			}
		}

		pthread_mutex_unlock(&mospf_lock);

		if (changed) {
			mospf_db_dump();
			mospf_update_rtable();
		}
	}
}

void handle_mospf_hello(iface_info_t *iface, const char *packet, int len)
{
	struct iphdr *ip = packet_to_ip_hdr(packet);
	struct mospf_hdr *mospf = (struct mospf_hdr *)IP_DATA(ip);
	struct mospf_hello *hello = (struct mospf_hello *)((char *)mospf + MOSPF_HDR_SIZE);

	if (ntohl(ip->saddr) == iface->ip)
		return;

	if (ntohs(mospf->len) < MOSPF_HDR_SIZE + MOSPF_HELLO_SIZE) {
		return;
	}
	if (ntohl(hello->mask) != iface->mask || (iface->ip & iface->mask) != (ntohl(ip->saddr) & iface->mask)) {
		return;
	}

	update_nbr_list_via_hello(iface, ntohl(mospf->rid), ntohl(ip->saddr), 
								ntohl(hello->mask), ntohs(hello->helloint));
}

static char *generate_mospf_lsu(int *lsu_len)
{
	int nadv = 0;
	iface_info_t *iface = NULL;
	list_for_each_entry(iface, &instance->iface_list, list)
		nadv += (iface->num_nbr == 0) ? 1 : iface->num_nbr;

	int len = MOSPF_HDR_SIZE + MOSPF_LSU_SIZE + nadv * MOSPF_LSA_SIZE;
	char *buf = (char *)malloc(len);
	memset(buf, 0, len);

	struct mospf_hdr *mospf = (struct mospf_hdr *)buf;
	struct mospf_lsu *lsu = (struct mospf_lsu *)(buf + MOSPF_HDR_SIZE);
	struct mospf_lsa *lsa = (struct mospf_lsa *)(buf + MOSPF_HDR_SIZE + MOSPF_LSU_SIZE);

	mospf_init_hdr(mospf, MOSPF_TYPE_LSU, len, instance->router_id, instance->area_id);
	mospf_init_lsu(lsu, nadv);

	list_for_each_entry(iface, &instance->iface_list, list) {
		if (iface->num_nbr == 0) {
			lsa->network = htonl(iface->ip & iface->mask);
			lsa->mask    = htonl(iface->mask);
			lsa->rid     = 0;
			lsa++;
		}
		else {
			mospf_nbr_t *nbr = NULL;
			list_for_each_entry(nbr, &iface->nbr_list, list) {
				lsa->network = htonl(nbr->nbr_ip & nbr->nbr_mask);
				lsa->mask    = htonl(nbr->nbr_mask);
				lsa->rid     = htonl(nbr->nbr_id);
				lsa++;
			}
		}
	}

	mospf->checksum = mospf_checksum(mospf);
	*lsu_len = len;

	return buf;
}

static void send_mospf_lsu(void)
{
	int lsu_len = 0;
	char *lsu_buf = generate_mospf_lsu(&lsu_len);

	iface_info_t *iface = NULL;
	list_for_each_entry(iface, &instance->iface_list, list) {
		mospf_nbr_t *nbr = NULL;
		list_for_each_entry(nbr, &iface->nbr_list, list) {
			int ip_len  = lsu_len + IP_BASE_HDR_SIZE;
			int pkt_len = ETHER_HDR_SIZE + ip_len;

			char *packet = (char *)malloc(pkt_len);
			memcpy(packet + ETHER_HDR_SIZE + IP_BASE_HDR_SIZE, lsu_buf, lsu_len);

			struct iphdr *ip = packet_to_ip_hdr(packet);
			ip_init_hdr(ip, iface->ip, nbr->nbr_ip, ip_len, IPPROTO_MOSPF);

			ip_send_packet(packet, pkt_len);
		}
	}

	free(lsu_buf);
}


void *sending_mospf_lsu_thread(void *param)
{
	while (1) {
		sleep(1);

		instance->lsuint -= 1;
		if (instance->lsuint <= 0) {
			instance->sequence_num += 1;
			send_mospf_lsu();
			instance->lsuint = MOSPF_DEFAULT_LSUINT;
		}
	}

	return NULL;
}

static void forward_mospf_lsu(iface_info_t *in_iface, char *packet, int len)
{
	struct iphdr *ip = packet_to_ip_hdr(packet);

	ip->ttl -= 1;
	if (ip->ttl == 0)
		return;

	iface_info_t *iface = NULL;
	list_for_each_entry(iface, &instance->iface_list, list) {
		if (iface->index == in_iface->index)
			continue;

		mospf_nbr_t *nbr = NULL;
		list_for_each_entry(nbr, &iface->nbr_list, list) {
			char *new_packet = (char *)malloc(len);
			memcpy(new_packet, packet, len);

			struct ether_header *eh = (struct ether_header *)new_packet;
			struct iphdr *new_ip = packet_to_ip_hdr(new_packet);

			memcpy(eh->ether_shost, iface->mac, ETH_ALEN);
			new_ip->saddr    = htonl(iface->ip);
			new_ip->daddr    = htonl(nbr->nbr_ip);
			new_ip->checksum = ip_checksum(new_ip);

			ip_send_packet(new_packet, len);	// forward
		}
	}
}

void handle_mospf_lsu(iface_info_t *iface, char *packet, int len)
{
	struct iphdr *ip = packet_to_ip_hdr(packet);
	struct mospf_hdr *mospf = (struct mospf_hdr *)IP_DATA(ip);
	struct mospf_lsu *lsu = (struct mospf_lsu *)((char *)mospf + MOSPF_HDR_SIZE);
	struct mospf_lsa *lsa = (struct mospf_lsa *)((char *)lsu + MOSPF_LSU_SIZE);

	u32 rid = ntohl(mospf->rid);

	if (rid == instance->router_id)
		return;

	u16 seq  = ntohs(lsu->seq);
	int nadv = ntohl(lsu->nadv);

	mospf_db_entry_t *entry = mospf_db_lookup(rid);
	if (entry && (short)(seq - entry->seq) <= 0) {
		return;
	}

	mospf_db_update(rid, seq, nadv, (const char *)lsa);

	lsu->ttl -= 1;
	mospf->checksum = mospf_checksum(mospf);

	if (lsu->ttl != 0)
		forward_mospf_lsu(iface, packet, len);

	mospf_update_rtable();
}

void handle_mospf_packet(iface_info_t *iface, char *packet, int len)
{
	struct iphdr *ip = (struct iphdr *)(packet + ETHER_HDR_SIZE);
	struct mospf_hdr *mospf = (struct mospf_hdr *)((char *)ip + IP_HDR_SIZE(ip));

	if (mospf->version != MOSPF_VERSION) {
		log(ERROR, "received mospf packet with incorrect version (%d)", mospf->version);
		return ;
	}
	if (mospf->checksum != mospf_checksum(mospf)) {
		log(ERROR, "received mospf packet with incorrect checksum");
		return ;
	}
	if (ntohl(mospf->aid) != instance->area_id) {
		log(ERROR, "received mospf packet with incorrect area id");
		return ;
	}

	switch (mospf->type) {
		case MOSPF_TYPE_HELLO:
			handle_mospf_hello(iface, packet, len);
			break;
		case MOSPF_TYPE_LSU:
			handle_mospf_lsu(iface, packet, len);
			break;
		default:
			log(ERROR, "received mospf packet with unknown type (%d).", mospf->type);
			break;
	}
}

// check if already in list
static int rid_index(const u32 *nodes, int n, u32 rid)
{
	for (int i = 0; i < n; i++)
		if (nodes[i] == rid)
			return i;
	return -1;
}

static u32 find_rid_ip(u32 rid, iface_info_t **iface_out)
{
	iface_info_t *iface = NULL;

	list_for_each_entry(iface, &instance->iface_list, list) {
		mospf_nbr_t *nbr = NULL;
		list_for_each_entry(nbr, &iface->nbr_list, list) {
			if (nbr->nbr_id == rid) {
				*iface_out = iface;
				return nbr->nbr_ip;
			}
		}
	}

	return 0;
}

static int dest_in_rtable(u32 dest, u32 mask)
{
	rt_entry_t *entry = NULL;

	list_for_each_entry(entry, &rtable, list) {
		if (entry->dest == dest && entry->mask == mask)
			return 1;
	}

	return 0;
}

void mospf_update_rtable(void)
{
	int max_nodes = 1;
	mospf_db_entry_t *entry = NULL;

	list_for_each_entry(entry, &mospf_db, list)
		max_nodes += 1 + entry->nadv;

	u32 *nodes = (u32 *)malloc(sizeof(u32) * max_nodes);
	int n = 0;

	nodes[n++] = instance->router_id;	// nodes[0] is self

	list_for_each_entry(entry, &mospf_db, list) {
		if (rid_index(nodes, n, entry->rid) < 0)	// new node
			nodes[n++] = entry->rid;

		for (int i = 0; i < entry->nadv; i++) {
			u32 rid = entry->array[i].rid;
			if (rid != 0 && rid_index(nodes, n, rid) < 0)
				nodes[n++] = rid;
		}
	}

	int *graph   = (int *)calloc(n * n, sizeof(int));	// build array to access
	int *dist    = (int *)malloc(sizeof(int) * n);
	int *prev    = (int *)malloc(sizeof(int) * n);
	int *visited = (int *)calloc(n, sizeof(int));

	list_for_each_entry(entry, &mospf_db, list) {
		int i = rid_index(nodes, n, entry->rid);
		if (i < 0)
			continue;

		for (int k = 0; k < entry->nadv; k++) {
			u32 rid = entry->array[k].rid;
			if (rid == 0)
				continue;

			int j = rid_index(nodes, n, rid);
			if (j < 0 || j == i)
				continue;

			graph[i * n + j] = 1;
			graph[j * n + i] = 1;
		}
	}

	for (int i = 0; i < n; i++) {
		dist[i] = INT_MAX;
		prev[i] = -1;	// flag
	}
	dist[0] = 0;

	// dijkstra
	for (int round = 0; round < n; round++) {
		int u = -1;
		for (int i = 0; i < n; i++) {
			if (visited[i] || dist[i] == INT_MAX)
				continue;
			if (u < 0 || dist[i] < dist[u])
				u = i;
		}
		if (u < 0)
			break;

		visited[u] = 1;
		for (int v = 0; v < n; v++) {
			if (!graph[u * n + v] || visited[v])
				continue;
			if (dist[v] > dist[u] + 1) {
				dist[v] = dist[u] + 1;
				prev[v] = u;
			}
		}
	}

	clear_rtable();

	iface_info_t *iface = NULL;
	list_for_each_entry(iface, &instance->iface_list, list) {
		rt_entry_t *rt = new_rt_entry(iface->ip & iface->mask, iface->mask, 0, iface);
		add_rt_entry(rt);
	}

	for (int d = 1; d < n; d++) {
		for (int i = 1; i < n; i++) {
			if (dist[i] != d)	// closer ones on top
				continue;

			int hop = i;
			while (prev[hop] != 0 && prev[hop] != -1)	// find next hop
				hop = prev[hop];

			iface_info_t *next_iface = NULL;
			u32 gw = find_rid_ip(nodes[hop], &next_iface);
			if (gw == 0 || next_iface == NULL) {
				continue;
			}

			entry = mospf_db_lookup(nodes[i]);
			if (!entry)
				continue;

			for (int k = 0; k < entry->nadv; k++) {
				u32 mask = entry->array[k].mask;
				u32 dest = entry->array[k].network & mask;

				if (mask == 0 || dest_in_rtable(dest, mask))
					continue;

				add_rt_entry(new_rt_entry(dest, mask, gw, next_iface));
			}
		}
	}

	free(nodes);
	free(graph);
	free(dist);
	free(prev);
	free(visited);

	mospf_db_dump();
}
