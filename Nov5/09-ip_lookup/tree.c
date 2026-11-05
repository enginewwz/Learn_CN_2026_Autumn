#include "tree.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// return an array of ip represented by an unsigned integer, the length of array is TEST_SIZE
uint32_t* read_test_data(const char* lookup_file)
{
    FILE* fp = fopen(lookup_file, "r");

    uint32_t* ip_vec = malloc(sizeof(uint32_t) * TEST_SIZE);

    for (int i = 0; i < TEST_SIZE; i++) {
        unsigned int a, b, c, d;
        if (fscanf(fp, "%u.%u.%u.%u", &a, &b, &c, &d) != 4) {
            fprintf(stderr, "bad data at line %d in %s\n", i + 1, lookup_file);
            free(ip_vec);
            fclose(fp);
            exit(1);
        }
        ip_vec[i] = MASK(a, 24) | MASK(b, 16) | MASK(c, 8) | MASK(d, 0);
    }

    fclose(fp);
    return ip_vec;
}

static node_t* basic_tree;

static node_t* create_node()
{
    node_t* p = malloc(sizeof(node_t));
    p->type = I_NODE;
    p->lchild = NULL;
    p->rchild = NULL;
    return p;
}

static void attach_node_to_basic_tree(fw_line_t line)
{
    if (!basic_tree)
    {
        basic_tree = create_node();
    }
    node_t* p = basic_tree;

    for (int i = 31; i > 31 - (int)line.mask; i --)
    {
        int right = ip_bit(line.ip, i);
        if (right)
        {
            if (!p->rchild) p->rchild = create_node();
            p = p->rchild;
        }
        else
        {
            if (!p->lchild) p->lchild = create_node();
            p = p->lchild;
        }
    }

    p->type = M_NODE;
    p->port = line.port;
}

// Constructing an basic trie-tree to lookup according to `forward_file`
void create_tree(const char* forward_file)
{
    FILE* fp = fopen(forward_file, "r");
    fw_line_t line;

    for (int i = 0; i < TRAIN_SIZE; i++) {
        unsigned int a, b, c, d, e, f;
        if (fscanf(fp, "%u.%u.%u.%u %u %u", &a, &b, &c, &d, &e, &f) != 6) {
            fprintf(stderr, "bad data at line %d in %s\n", i + 1, forward_file);
            fclose(fp);
            exit(1);
        }
        line.ip = MASK(a, 24) | MASK(b, 16) | MASK(c, 8) | MASK(d, 0);
        line.mask = e;
        line.port = f;
        attach_node_to_basic_tree(line);
    }

    fclose(fp);
}

// Look up the ports of ip in file `ip_to_lookup.txt` using the basic tree, input is read from `read_test_data` func 
uint32_t *lookup_tree(uint32_t* ip_vec)
{
    uint32_t* res = malloc(sizeof(uint32_t) * TEST_SIZE);
    node_t* p;
    uint32_t best_port;

    for (int i = 0; i < TEST_SIZE; i ++)
    {
        uint32_t ip = *(ip_vec + i);
        p = basic_tree;
        best_port = -1;
        
        for (int j = 31; j > -1 && p; j --)
        {
            if (p->type == M_NODE) best_port = p->port;
            int right = ip_bit(ip, j);
            p = (right) ? p->rchild : p->lchild;
        }
        
        *(res + i) = best_port;
    }

    return res;
}


// simple poptrie

// use the basic tree generate as an base

static node_t* poptrie_helper_tree;

static void attach_node_to_poptrie_helper_tree(fw_line_t line)
{
    if (!poptrie_helper_tree)
    {
        poptrie_helper_tree = create_node();
    }
    node_t* p = poptrie_helper_tree;

    for (int i = 31; i > 31 - (int)line.mask; i --)
    {
        int right = ip_bit(line.ip, i);
        if (right)
        {
            if (!p->rchild) p->rchild = create_node();
            p = p->rchild;
        }
        else
        {
            if (!p->lchild) p->lchild = create_node();
            p = p->lchild;
        }
    }

    p->type = M_NODE;
    p->port = line.port;
}

static void create_poptrie_helper_tree(const char* forward_file)
{
    FILE* fp = fopen(forward_file, "r");
    fw_line_t line;

    for (int i = 0; i < TRAIN_SIZE; i++) {
        unsigned int a, b, c, d, e, f;
        if (fscanf(fp, "%u.%u.%u.%u %u %u", &a, &b, &c, &d, &e, &f) != 6) {
            fprintf(stderr, "bad data at line %d in %s\n", i + 1, forward_file);
            fclose(fp);
            exit(1);
        }
        line.ip = MASK(a, 24) | MASK(b, 16) | MASK(c, 8) | MASK(d, 0);
        line.mask = e;
        line.port = f;
        attach_node_to_poptrie_helper_tree(line);
    }

    fclose(fp);
}

static void destory_helper_tree_node(node_t* p)
{
    if (p->lchild) destory_helper_tree_node(p->lchild);
    if (p->rchild) destory_helper_tree_node(p->rchild);
    free(p);
}

static inline void destory_helper_tree()
{
    if (poptrie_helper_tree) destory_helper_tree_node(poptrie_helper_tree);
}

// simple queue func
static inline int que_empty(que* q)
{
    return q->head == q->tail;
}

static inline void initial_que(que* q)
{
    q->head = 0;
    q->tail = 0;
}

static inline void enque(que* q, que_t input)
{
    q->element[q->tail] = input;
    q->tail = (q->tail + 1) % MAXQUE;
}

static inline que_t deque(que* q)
{
    que_t output = q->element[q->head];
    q->head = (q->head + 1) % MAXQUE;
    return output;
}

// use helper tree to build poptrie
poptrie_node_t* poptrie_tree;

static poptrie_node_t* create_temp_poptrie_node(node_t* helper_root)
{
    struct temp_poptrie_node* p = malloc(sizeof(struct temp_poptrie_node));
    p->node = malloc(sizeof(poptrie_node_t));
    p->helper_root_node = helper_root;
    p->child_nodes_cnt = 0;
    p->ports_cnt = 0;
    p->node->p = p;
    return p->node;
}

static int free_temp_poptrie_node(poptrie_node_t* node, poptrie_node_t** node_buffer, uint32_t* ports_buffer)
{
    struct temp_poptrie_node* temp_root = node->p;

    size_t child_nodes_size = sizeof(poptrie_node_t*) * temp_root->child_nodes_cnt;
    node->child_nodes = malloc(child_nodes_size);
    memcpy(node->child_nodes, node_buffer, child_nodes_size);

    size_t ports_size = sizeof(uint32_t) * temp_root->ports_cnt;
    node->ports = malloc(ports_size);
    memcpy(node->ports, ports_buffer, ports_size);

    int child_cnt = temp_root->child_nodes_cnt;
    node->p = NULL;
    free(temp_root);
    return child_cnt;
}

// no need to treat last several nodes differently for they have no children
static void create_poptire_node(poptrie_node_t* node, poptrie_node_t** node_buffer, uint32_t* ports_buffer,
                                que* queue)
{
    node->child_bitmap = 0;
    node->inner_bitmap = 0;
    struct temp_poptrie_node* temp_root = node->p;

    node_t* basic_root = temp_root->helper_root_node;

    initial_que(queue);
    que_t que_input = {.basic_node = basic_root, .depth = 0, .num = 0};
    enque(queue, que_input);

    while (!que_empty(queue))
    {
        que_t que_output = deque(queue);
        node_t* basic_node = que_output.basic_node;
        int depth = que_output.depth;
        int num = que_output.num;

        if (depth < 6)
        {            
            if (basic_node->type == M_NODE)
            {
                node->inner_bitmap |= inner_mask(num, depth);
                ports_buffer[temp_root->ports_cnt ++] = basic_node->port;
            }

            depth ++;
            if (basic_node->lchild)
            {
                que_t que_input = {.basic_node = basic_node->lchild, .depth = depth, .num = num << 1};
                enque(queue, que_input);
            }
            if (basic_node->rchild)
            {
                que_t que_input = {.basic_node = basic_node->rchild, .depth = depth, .num = (num << 1) + 1};
                enque(queue, que_input);
            }
        }
        else
        {
            node->child_bitmap |= child_mask(num);
            node_buffer[temp_root->child_nodes_cnt ++] = create_temp_poptrie_node(basic_node);
        }
    }
}

static void create_child_poptire_node(poptrie_node_t* node, poptrie_node_t** node_buffer, uint32_t* ports_buffer,
                                        que* queue)
{
    create_poptire_node(node, node_buffer, ports_buffer, queue);
    int child_cnt = free_temp_poptrie_node(node, node_buffer, ports_buffer);

    for (int i = 0; i < child_cnt; i ++)
    {
        create_child_poptire_node(node->child_nodes[i], node_buffer, ports_buffer, queue);
    }
}

// struct: uint32: 32 -> 27 63 inner nodes + 26 64 leaf nodes
// 32 -> 27 26; 26 -> 21 20; 20 -> 15 14; 14 -> 9 8; 8 -> 3 2; 2 -> 0
static void create_poptrie_tree()
{   
    poptrie_node_t** node_buffer = malloc(sizeof(poptrie_node_t*) * 64); 
    uint32_t* ports_buffer = malloc(sizeof(uint32_t) * 63);
    que* queue = malloc(sizeof(que));
    poptrie_tree = create_temp_poptrie_node(poptrie_helper_tree);
    create_child_poptire_node(poptrie_tree, node_buffer, ports_buffer, queue);
    free(node_buffer);
    free(ports_buffer);
    free(queue);
}

// Constructing an advanced trie-tree to lookup according to `forward_file`
void create_tree_advance(const char* forward_file)
{
    create_poptrie_helper_tree(forward_file);
    create_poptrie_tree();
    destory_helper_tree();
}

static uint64_t search_mask[64];

static void init_search_mask()
{
    for (int i = 0; i < 64; i ++) {
        uint64_t k = 0;
        for (int j = 0; j < 6; j ++) {
            int num = j ? (i >> (6 - j)) : 0u;
            k |= inner_mask(num, j);
        }
        search_mask[i] = k;
    }
}

// Look up the ports of ip in file `ip_to_lookup.txt` using the advanced tree input is read from `read_test_data` func 
uint32_t *lookup_tree_advance(uint32_t* ip_vec)
{
    init_search_mask();

    uint32_t* res = malloc(sizeof(uint32_t) * TEST_SIZE);
    poptrie_node_t* p;
    uint32_t best_port;

    for (int i = 0; i < TEST_SIZE; i ++)
    {
        uint32_t ip = *(ip_vec + i);
        p = poptrie_tree;
        best_port = -1;
        
        int k;
        for (k = 32; k > -1; k -= 6)
        {
            uint64_t inner_bitmap = p->inner_bitmap, child_bitmap = p->child_bitmap;
            int num = ip >> 26;

            uint64_t hit = inner_bitmap & search_mask[num];
            if (hit) 
            {
                int pos = 63 - __builtin_clzll(hit);
                uint64_t best_mask = ((uint64_t) 1 << pos) - 1;
                best_port = p->ports[__builtin_popcountll(inner_bitmap & best_mask)];
            }

            if (k < 6) break;
            
            uint64_t child_mask = child_mask(num);
            uint64_t child_hit = child_mask(num) & child_bitmap;
            int index = __builtin_popcountll(child_bitmap & (child_mask - 1));
            if (child_hit) p = p->child_nodes[index];
            else break;
            ip <<= 6;
        }

        *(res + i) = best_port;
    }

    return res;
}
