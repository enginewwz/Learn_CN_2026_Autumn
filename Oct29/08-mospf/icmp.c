#include "icmp.h"
#include "ip.h"
#include "rtable.h"
#include "arp.h"
#include "base.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// send icmp packet
void icmp_send_packet(const char *in_pkt, int len, u8 type, u8 code)
{
	struct iphdr *in_ip = packet_to_ip_hdr(in_pkt);
	struct icmphdr *in_icmp = (struct icmphdr *) IP_DATA(in_ip);

	int icmp_len, ip_len;

	if (type == ICMP_ECHOREPLY) 
	{
		icmp_len = ntohs(in_ip->tot_len) - IP_HDR_SIZE(in_ip);
		ip_len   = len;
	}
	else 
	{
		icmp_len = IP_HDR_SIZE(in_ip) + ICMP_HDR_SIZE + ICMP_COPIED_DATA_LEN;
		ip_len   = ETHER_HDR_SIZE + IP_BASE_HDR_SIZE + icmp_len;
	}

	char *packet = (char *)malloc(ip_len);
	memset(packet, 0, ip_len);

	struct iphdr *ip = packet_to_ip_hdr(packet);
	struct icmphdr *icmp = (struct icmphdr *)((char *)ip + IP_BASE_HDR_SIZE);

	icmp->type = type;
	icmp->code = code;

	if (type == ICMP_ECHOREPLY) {
		icmp->icmp_identifier = in_icmp->icmp_identifier;
		icmp->icmp_sequence   = in_icmp->icmp_sequence;
		memcpy((char *)icmp + ICMP_HDR_SIZE,
				(char *)in_icmp + ICMP_HDR_SIZE, icmp_len - ICMP_HDR_SIZE);
	}
	else {
		memcpy((char *)icmp + ICMP_HDR_SIZE,
				(char *)in_ip, icmp_len - ICMP_HDR_SIZE);
	}

	icmp->checksum = icmp_checksum(icmp, icmp_len);

	u32 daddr = ntohl(in_ip->saddr);
	u32 saddr;

	if (type == ICMP_ECHOREPLY) 
	{
		saddr = ntohl(in_ip->daddr);
	}
	else {
		rt_entry_t *entry = longest_prefix_match(daddr);
		if (!entry) 
		{
			free(packet);
			return;
		}
		saddr = entry->iface->ip;
	}

	ip_init_hdr(ip, saddr, daddr, icmp_len + IP_BASE_HDR_SIZE, IPPROTO_ICMP);
	ip_send_packet(packet, ip_len);
}
