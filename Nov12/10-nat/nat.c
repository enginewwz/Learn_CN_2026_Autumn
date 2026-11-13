#include "nat.h"
#include "ip.h"
#include "icmp.h"
#include "tcp.h"
#include "rtable.h"
#include "log.h"

#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>

static struct nat_table nat;

// get the interface from iface name
static iface_info_t *if_name_to_iface(const char *if_name)
{
	iface_info_t *iface = NULL;
	list_for_each_entry(iface, &instance->iface_list, list) {
		if (strcmp(iface->name, if_name) == 0)
			return iface;
	}

	log(ERROR, "Could not find the desired interface according to if_name '%s'", if_name);
	return NULL;
}

// determine the direction of the packet, DIR_IN / DIR_OUT / DIR_INVALID
static int get_packet_direction(char *packet)
{
	struct iphdr *ip = packet_to_ip_hdr(packet);
	rt_entry_t* src = longest_prefix_match(ntohl(ip->saddr));
	rt_entry_t* dst = longest_prefix_match(ntohl(ip->daddr));

	// DIR IN
	if (src && src->iface->index == nat.external_iface->index && ntohl(ip->daddr) == nat.external_iface->ip)
		return DIR_IN;
	// DIR OUT
	if (dst && dst->iface->index == nat.external_iface->index && src && src->iface->index == nat.internal_iface->index)
		return DIR_OUT;

	return DIR_INVALID;
}

static u8 nat_hash(u32 remote_ip, u16 remote_port)
{
	return hash8((char *) &remote_ip, 4) ^ hash8((char *) &remote_port, 2);
}

// check whether the flow is finished according to FIN bit and sequence number
// XXX: seq_end is calculated by `tcp_seq_end` in tcp.h
static int is_flow_finished(struct nat_connection *conn)
{
    return (conn->internal_fin && conn->external_fin) && \
            (conn->internal_ack >= conn->external_seq_end) && \
            (conn->external_ack >= conn->internal_seq_end);
}

static struct nat_mapping* nat_lookup_external(struct list_head *mapping_list, u16 ext_port)
{
	struct nat_mapping *mapping;

	pthread_mutex_lock(&nat.lock);
	list_for_each_entry(mapping, mapping_list, list) 
	{
		if (mapping->external_port == ext_port) 
		{
			mapping->update_time = time(NULL);
			pthread_mutex_unlock(&nat.lock);
			return mapping;
		}
	}
	pthread_mutex_unlock(&nat.lock);

	return NULL;
}

static struct nat_mapping *nat_lookup_internal(struct list_head *mapping_list, u32 int_ip, u16 int_port)
{
	struct nat_mapping *mapping;

	pthread_mutex_lock(&nat.lock);
	list_for_each_entry(mapping, mapping_list, list) 
	{
		if (mapping->internal_ip == int_ip && mapping->internal_port == int_port) 
		{
			mapping->update_time = time(NULL);
			pthread_mutex_unlock(&nat.lock);
			return mapping;
		}
	}
	pthread_mutex_unlock(&nat.lock);

	return NULL;
}

static u16 assign_external_port(void)
{
	for (int port = NAT_PORT_MIN; port <= NAT_PORT_MAX; port++) 
	{
		if (!nat.assigned_ports[port]) 
		{
			nat.assigned_ports[port] = 1;
			return (u16)port;
		}
	}
	return 0;	// not found
}

static void free_port(u16 port)
{
	nat.assigned_ports[port] = 0;
}

static struct dnat_rule *dnat_rule_lookup(u32 ext_ip, u16 ext_port)
{
	struct dnat_rule *entry;

	list_for_each_entry(entry, &nat.rules, list) {
		if (entry->external_ip == ext_ip && entry->external_port == ext_port)
			return entry;
	}

	return NULL;
} 

static struct nat_mapping *nat_insert_mapping(struct list_head *mapping_list,
		u32 int_ip, u16 int_port, u32 ext_ip, u16 ext_port)
{
	struct nat_mapping *mapping = malloc(sizeof(struct nat_mapping));
	memset(mapping, 0, sizeof(struct nat_mapping));
	
	mapping->internal_ip   = int_ip;
	mapping->internal_port = int_port;
	mapping->external_ip   = ext_ip;
	mapping->external_port = ext_port;
	
	pthread_mutex_lock(&nat.lock);
	
	mapping->update_time   = time(NULL);
	list_add_head(&mapping->list, mapping_list);

	pthread_mutex_unlock(&nat.lock);
	return mapping;
}

static void nat_update_tcp_connection(char *packet, struct nat_mapping *mapping, int dir)
{
	struct iphdr *ip = packet_to_ip_hdr(packet);
	struct tcphdr *tcp = packet_to_tcp_hdr(packet);
	u32 seq_end = tcp_seq_end(ip, tcp);
	u32 ack = ntohl(tcp->ack);

	pthread_mutex_lock(&nat.lock);

	mapping->update_time = time(NULL);
	struct nat_connection *conn = &mapping->conn;

	if (dir == DIR_IN) 
	{
		if (tcp->flags & TCP_FIN)
			conn->external_fin = 1;
		if (conn->external_fin)
			conn->external_seq_end = seq_end;
		if (conn->internal_fin)
			conn->external_ack = ack;
	}
	else 
	{
		if (tcp->flags & TCP_FIN)
			conn->internal_fin = 1;
		if (conn->internal_fin)
			conn->internal_seq_end = seq_end;
		if (conn->internal_fin)
			conn->internal_ack = ack;
	}

	if (tcp->flags & TCP_RST || is_flow_finished(& mapping->conn))
	{
		free_port(mapping->external_port);
		list_delete_entry(&mapping->list);
		free(mapping);
	}

	pthread_mutex_unlock(&nat.lock);
}

static struct nat_mapping *nat_get_mapping_from_packet(char *packet, int dir)
{
	struct iphdr *ip = packet_to_ip_hdr(packet);
	struct tcphdr *tcp = packet_to_tcp_hdr(packet);
	struct nat_mapping *mapping = NULL;
	struct list_head *mapping_list;
	int hash;

	if (dir == DIR_IN) 
	{
		u32 ext_ip      = ntohl(ip->daddr);
		u16 ext_port    = ntohs(tcp->dport);
		u32 remote_ip   = ntohl(ip->saddr);
		u16 remote_port = ntohs(tcp->sport);

		hash = nat_hash(remote_ip, remote_port);
		mapping_list = &nat.nat_mapping_list[hash];

		mapping = nat_lookup_external(mapping_list, ext_port);
		if (mapping)
			goto out;

		// only a SYN packet can create a new incoming mapping
		if (tcp->flags != TCP_SYN) 
		{
			log(ERROR, "could not get mapping for non-SYN ingoing packet.");
			goto out;
		}

		struct dnat_rule *rule = dnat_rule_lookup(ext_ip, ext_port);
		if (!rule) 
		{
			log(ERROR, "could not find the dnat rule for ingoing SYN packet.");
			goto out;
		}

		u32 int_ip   = rule->internal_ip;
		u16 int_port = rule->internal_port;
		mapping = nat_insert_mapping(mapping_list, int_ip, int_port, ext_ip, ext_port);
	}
	else 
	{
		u32 int_ip      = ntohl(ip->saddr);
		u16 int_port    = ntohs(tcp->sport);
		u32 remote_ip   = ntohl(ip->daddr);
		u16 remote_port = ntohs(tcp->dport);

		hash = nat_hash(remote_ip, remote_port);
		mapping_list = &nat.nat_mapping_list[hash];

		mapping = nat_lookup_internal(mapping_list, int_ip, int_port);
		if (mapping)
			goto out;

		// only a SYN packet can create a new outgoing mapping
		if (tcp->flags != TCP_SYN) 
		{
			log(ERROR, "could not get mapping for non-SYN outgoing packet.");
			goto out;
		}

		// assign new port
		u32 ext_ip   = nat.external_iface->ip;
		u16 ext_port = assign_external_port();
		mapping = nat_insert_mapping(mapping_list, int_ip, int_port, ext_ip, ext_port);
	}

out:
	return mapping;
}

// do translation for the packet: replace the ip/port, recalculate ip & tcp
// checksum, update the statistics of the tcp connection
void do_translation(iface_info_t *iface, char *packet, int len, int dir)
{
	struct nat_mapping* mapping = nat_get_mapping_from_packet(packet, dir);

	if (!mapping) {
		log(ERROR, "could not find mapping for the packet.");
		free(packet);
		return;
	}
	
	struct iphdr* ip = packet_to_ip_hdr(packet);
	struct tcphdr* tcp = packet_to_tcp_hdr(packet);

	if (dir == DIR_IN)
	{
		ip->daddr = htonl(mapping->internal_ip);
		tcp->dport = htons(mapping->internal_port);
	}
	else
	{
		ip->saddr = htonl(mapping->external_ip);
		tcp->sport = htons(mapping->external_port);
	}

	tcp->checksum = tcp_checksum(ip, tcp);
	ip->checksum = ip_checksum(ip);
	
	nat_update_tcp_connection(packet, mapping, dir);

	ip_send_packet(packet, len);
}

void nat_translate_packet(iface_info_t *iface, char *packet, int len)
{
	int dir = get_packet_direction(packet);
	if (dir == DIR_INVALID) {
		log(ERROR, "invalid packet direction, drop it.");
		icmp_send_packet(packet, len, ICMP_DEST_UNREACH, ICMP_HOST_UNREACH);
		free(packet);
		return ;
	}

	struct iphdr *ip = packet_to_ip_hdr(packet);
	if (ip->protocol != IPPROTO_TCP) {
		log(ERROR, "received non-TCP packet (0x%0hhx), drop it", ip->protocol);
		free(packet);
		return ;
	}

	do_translation(iface, packet, len, dir);
}


// nat timeout thread: find the finished flows, remove them and free port
// resource
void *nat_timeout(void* arg)
{
	while (1) {
		pthread_mutex_lock(&nat.lock);

		time_t cur_time = time(NULL);

		for (int i = 0; i < HASH_8BITS; i ++)
		{
			struct nat_mapping *mapping, *q;
			list_for_each_entry_safe(mapping, q, &nat.nat_mapping_list[i], list)
			{
				if (is_flow_finished(& mapping->conn) || difftime(cur_time, mapping->update_time) >= TCP_ESTABLISHED_TIMEOUT)
				{
					free_port(mapping->external_port);
					list_delete_entry(&mapping->list);
					free(mapping);
				}
			}
		}

		pthread_mutex_unlock(&nat.lock);

		sleep(1);
	}

	return NULL;
}

#define MAX_CONFIG_TOKENS 10
#define MAX_TOKEN_LEN     100

static int get_next_line(FILE *input, char (*strs)[MAX_TOKEN_LEN], int *num_strings)
{
	const char *delim = " \t\n";
	char buffer[120];
	int ret = 0;

	if (fgets(buffer, sizeof(buffer), input)) 
	{
		char *token = strtok(buffer, delim);

		*num_strings = 0;
		while (token) 
		{
			strcpy(strs[*num_strings], token);
			(*num_strings)++;
			token = strtok(NULL, delim);
		}

		ret = 1;
	}

	return ret;
}

static int read_ip_port(const char *str, u32 *ip, u16 *port)
{
	int i1, i2, i3, i4;
	int ret = sscanf(str, "%d.%d.%d.%d:%hu", &i1, &i2, &i3, &i4, port);

	if (ret != 5) 
	{
		log(ERROR, "parse ip-port string error: %s.", str);
		exit(1);
	}

	*ip = ((u32)i1 << 24) | ((u32)i2 << 16) | ((u32)i3 << 8) | (u32)i4;
	return 0;
}

int parse_config(const char *filename)
{
	FILE *input = fopen(filename, "r");

	char strings[MAX_CONFIG_TOKENS][MAX_TOKEN_LEN];
	int num_strings;

	while (get_next_line(input, strings, &num_strings)) 
	{
		if (num_strings == 0)
			continue;

		if (strcmp(strings[0], "internal-iface:") == 0) 
		{
			nat.internal_iface = if_name_to_iface(strings[1]);
		}
		else if (strcmp(strings[0], "external-iface:") == 0) 
		{
			nat.external_iface = if_name_to_iface(strings[1]);
		}
		else if (strcmp(strings[0], "dnat-rules:") == 0) 
		{
			struct dnat_rule *rule = malloc(sizeof(struct dnat_rule));

			read_ip_port(strings[1], &rule->external_ip, &rule->external_port);
			read_ip_port(strings[3], &rule->internal_ip, &rule->internal_port);
			list_add_tail(&rule->list, &nat.rules);
		}
		else 
		{
			log(ERROR, "incorrect config file, exit.");
			exit(1);
		}
	}

	fclose(input);

	if (!nat.internal_iface || !nat.external_iface) {
		log(ERROR, "could not find the desired interfaces for nat.");
		exit(1);
	}

	return 0;
}

// initialize
void nat_init(const char *config_file)
{
	memset(&nat, 0, sizeof(nat));

	for (int i = 0; i < HASH_8BITS; i++)
		init_list_head(&nat.nat_mapping_list[i]);

	init_list_head(&nat.rules);

	// seems unnecessary
	memset(nat.assigned_ports, 0, sizeof(nat.assigned_ports));

	parse_config(config_file);

	pthread_mutex_init(&nat.lock, NULL);

	pthread_create(&nat.thread, NULL, nat_timeout, NULL);
}

void nat_exit()
{
	pthread_mutex_lock(&nat.lock);

	for (int i = 0; i < HASH_8BITS; i++) 
	{
		struct nat_mapping *mapping, *q;

		list_for_each_entry_safe(mapping, q, &nat.nat_mapping_list[i], list) 
		{
			list_delete_entry(&mapping->list);
			free(mapping);
		}
	}

	pthread_kill(nat.thread, SIGTERM);
	pthread_mutex_unlock(&nat.lock);
}
