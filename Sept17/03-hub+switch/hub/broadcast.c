#include "base.h"
#include <stdio.h>

extern ustack_t *instance;

// the memory of ``packet'' will be free'd in handle_packet().
void broadcast_packet(iface_info_t *iface, const char *packet, int len)
{
	iface_info_t *entry;
	list_for_each_entry(entry, &(instance->iface_list), list)	// defined in 03-hub+switch/hub/include/list.h
	{
		if (entry->index != iface->index)
		{
			iface_send_packet(entry, packet, len);
		}
	}
}
