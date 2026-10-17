#include "arpcache.h"
#include "arp.h"
#include "ether.h"
#include "icmp.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>

static arpcache_t arpcache;

// initialize IP->mac mapping, request list, lock and sweeping thread
void arpcache_init()
{
	bzero(&arpcache, sizeof(arpcache_t));

	init_list_head(&(arpcache.req_list));

	pthread_mutex_init(&arpcache.lock, NULL);

	pthread_create(&arpcache.thread, NULL, arpcache_sweep, NULL);
}

// release all the resources when exiting
void arpcache_destroy()
{
	pthread_mutex_lock(&arpcache.lock);

	struct arp_req *req_entry = NULL, *req_q;
	list_for_each_entry_safe(req_entry, req_q, &(arpcache.req_list), list) {
		struct cached_pkt *pkt_entry = NULL, *pkt_q;
		list_for_each_entry_safe(pkt_entry, pkt_q, &(req_entry->cached_packets), list) {
			list_delete_entry(&(pkt_entry->list));
			free(pkt_entry->packet);
			free(pkt_entry);
		}

		list_delete_entry(&(req_entry->list));
		free(req_entry);
	}

	pthread_kill(arpcache.thread, SIGTERM);

	pthread_mutex_unlock(&arpcache.lock);
}

// lookup the IP->mac mapping
//
// traverse the table to find whether there is an entry with the same IP
// and mac address with the given arguments
int arpcache_lookup(u32 ip4, u8 mac[ETH_ALEN])
{
	int found = 0;

	pthread_mutex_lock(&arpcache.lock);
	for (size_t i = 0; i < MAX_ARP_SIZE; i ++)
	{
		struct arp_cache_entry* cur_entry = &arpcache.entries[i];
		if ((found == 0) && (cur_entry->valid) && (cur_entry->ip4 == ip4))
		{
			memcpy(mac, cur_entry->mac, ETH_ALEN);
			found = 1;
		}
	}
	pthread_mutex_unlock(&arpcache.lock);

	return found;
}

// append the packet to arpcache
//
// Lookup in the list which stores pending packets, if there is already an
// entry with the same IP address and iface (which means the corresponding arp
// request has been sent out), just append this packet at the tail of that entry
// (the entry may contain more than one packet); otherwise, malloc a new entry
// with the given IP address and iface, append the packet, and send arp request.
void arpcache_append_packet(iface_info_t *iface, u32 ip4, char *packet, int len)
{
	struct arp_req *req_entry = NULL;
	struct cached_pkt *new_pkt = malloc(sizeof(struct cached_pkt));
	new_pkt->packet = packet;
	new_pkt->len = len;

	pthread_mutex_lock(&arpcache.lock);

	list_for_each_entry(req_entry, &(arpcache.req_list), list)
	{
		if ((req_entry->ip4 == ip4) && (req_entry->iface->index == iface->index))
		{
			list_add_tail(&new_pkt->list, &(req_entry->cached_packets));
			pthread_mutex_unlock(&arpcache.lock);
			return;
		}
	}
	
	struct arp_req *new_req = malloc(sizeof(struct arp_req));
	init_list_head(&new_req->list);
	init_list_head(&new_req->cached_packets); 

	new_req->iface = iface;
	new_req->ip4 = ip4;
	new_req->sent = time(NULL);
	new_req->retries = 0;
	
	list_add_tail(&new_pkt->list, &(new_req->cached_packets));	
	list_add_tail(&new_req->list, &(arpcache.req_list));

	pthread_mutex_unlock(&arpcache.lock);
	
	arp_send_request(iface, ip4);
}

// insert the IP->mac mapping into arpcache, if there are pending packets
// waiting for this mapping, fill the ethernet header for each of them, and send
// them out
void arpcache_insert(u32 ip4, u8 mac[ETH_ALEN])
{
	size_t pos_sel = MAX_ARP_SIZE;
	pthread_mutex_lock(&arpcache.lock);

	for (size_t i = 0; i < MAX_ARP_SIZE; i ++)
	{
		if (arpcache.entries[i].valid)
		{
			if (arpcache.entries[i].ip4 == ip4)
			{
				pos_sel = i;
				break;
			}
		}
		else
		{
			pos_sel = (i < pos_sel) ? i : pos_sel;
		}		
	}

	if (pos_sel == MAX_ARP_SIZE) pos_sel = rand() % MAX_ARP_SIZE;

	arpcache.entries[pos_sel].ip4 = ip4;
	memcpy(arpcache.entries[pos_sel].mac, mac, ETH_ALEN);
	arpcache.entries[pos_sel].added = time(NULL);
	arpcache.entries[pos_sel].valid = 1;

	struct arp_req *req_entry = NULL, *req_q;
	list_for_each_entry_safe(req_entry, req_q, &arpcache.req_list, list) 
	{
		if (req_entry->ip4 != ip4)
			continue;

		struct cached_pkt *pkt_entry = NULL, *pkt_q;
		list_for_each_entry_safe(pkt_entry, pkt_q, &req_entry->cached_packets, list) 
		{
			struct ether_header *eh = (struct ether_header *)pkt_entry->packet;
			memcpy(eh->ether_dhost, mac, ETH_ALEN);
			memcpy(eh->ether_shost, req_entry->iface->mac, ETH_ALEN);
			iface_send_packet(req_entry->iface, pkt_entry->packet, pkt_entry->len);
			list_delete_entry(&pkt_entry->list);
			free(pkt_entry);
		}

		list_delete_entry(&req_entry->list);
		free(req_entry);
	}

	pthread_mutex_unlock(&arpcache.lock);
}

// sweep arpcache periodically
//
// For the IP->mac entry, if the entry has been in the table for more than 15
// seconds, remove it from the table.
// For the pending packets, if the arp request is sent out 1 second ago, while 
// the reply has not been received, retransmit the arp request. If the arp
// request has been sent 5 times without receiving arp reply, for each
// pending packet, send icmp packet (DEST_HOST_UNREACHABLE), and drop these
// packets.
void *arpcache_sweep(void *arg) 
{
	while (1) {
		sleep(1);
		
		time_t cur_time = time(NULL);

		struct list_head timeout_list;
		init_list_head(&timeout_list);

		pthread_mutex_lock(&arpcache.lock);
		
		for (size_t i = 0; i < MAX_ARP_SIZE; i ++)
		{
			struct arp_cache_entry* cur_entry = &arpcache.entries[i];
			if (cur_entry->valid && (difftime(cur_time, cur_entry->added) > ARP_ENTRY_TIMEOUT))
			{
				cur_entry->valid = 0;
			}
		}

		struct arp_req *req_entry = NULL, *req_q;
		list_for_each_entry_safe(req_entry, req_q, &(arpcache.req_list), list) 
		{
			if (req_entry->retries == ARP_REQUEST_MAX_RETRIES)
			{
				list_delete_entry(&(req_entry->list));
				list_add_tail(&req_entry->list, &timeout_list);
			}
			else
			{
				if (difftime(cur_time, req_entry->sent) > 1)
				{
					arp_send_request(req_entry->iface, req_entry->ip4);
					req_entry->sent = time(NULL);
					(req_entry->retries)++;
				}
			}
		}

		pthread_mutex_unlock(&arpcache.lock);

		list_for_each_entry_safe(req_entry, req_q, &timeout_list, list) 
		{
			struct cached_pkt *pkt_entry = NULL, *pkt_q;
			list_for_each_entry_safe(pkt_entry, pkt_q, &req_entry->cached_packets, list) 
			{
				icmp_send_packet(pkt_entry->packet, pkt_entry->len, ICMP_DEST_UNREACH, ICMP_HOST_UNREACH);
				list_delete_entry(&pkt_entry->list);
				free(pkt_entry->packet);
				free(pkt_entry);
			}
			list_delete_entry(&req_entry->list);
			free(req_entry);
		}
	}

	return NULL;
}
