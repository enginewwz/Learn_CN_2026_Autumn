#include "mospf_database.h"
#include "ip.h"

#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <arpa/inet.h>

struct list_head mospf_db;

void init_mospf_db()
{
	init_list_head(&mospf_db);
}

mospf_db_entry_t *mospf_db_lookup(u32 rid)
{
	mospf_db_entry_t *entry = NULL;
	list_for_each_entry(entry, &mospf_db, list) {
		if (entry->rid == rid)
			return entry;
	}
	return NULL;
}

static struct mospf_lsa *copy_lsa_ntoh(const char *raw, int nadv)
{
	struct mospf_lsa *array = malloc(nadv * MOSPF_LSA_SIZE);

	for (int i = 0; i < nadv; i++) {
		const struct mospf_lsa *in = (const struct mospf_lsa *)raw + i;
		array[i].network = ntohl(in->network);
		array[i].mask    = ntohl(in->mask);
		array[i].rid     = ntohl(in->rid);
	}
	return array;
}

// new lsu package arrives
void mospf_db_update(u32 rid, u16 seq, int nadv, const char *lsa_raw)
{
	mospf_db_entry_t *entry = mospf_db_lookup(rid);

	if (!entry) {
		entry = malloc(sizeof(*entry));
		memset(entry, 0, sizeof(*entry));
		init_list_head(&entry->list);
		entry->rid = rid;
		list_add_tail(&entry->list, &mospf_db);
	}
	else {
		free(entry->array);
		entry->array = NULL;
	}

	entry->seq    = seq;
	entry->nadv   = nadv;
	entry->alive  = MOSPF_DATABASE_TIMEOUT;
	entry->array  = copy_lsa_ntoh(lsa_raw, nadv);

	mospf_db_dump();
}

void mospf_db_dump()
{
	mospf_db_entry_t *entry = NULL;

	fprintf(stdout, "MOSPF Database entries:\n");
	list_for_each_entry(entry, &mospf_db, list) {
		fprintf(stdout, "  rid " IP_FMT ", seq %hu, nadv %d, alive %d\n",
				HOST_IP_FMT_STR(entry->rid), entry->seq,
				entry->nadv, entry->alive);

		for (int i = 0; i < entry->nadv; i++) {
			fprintf(stdout, "    " IP_FMT "/" IP_FMT " -> rid " IP_FMT "\n",
					HOST_IP_FMT_STR(entry->array[i].network),
					HOST_IP_FMT_STR(entry->array[i].mask),
					HOST_IP_FMT_STR(entry->array[i].rid));
		}
	}
}