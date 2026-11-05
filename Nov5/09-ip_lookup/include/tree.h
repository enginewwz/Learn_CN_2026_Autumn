#ifndef __TREE_H__
#define __TREE_H__

#include <stdint.h>
#include <stdio.h>
#include <stdbool.h>
#include <stddef.h>

// do not change it
#define TEST_SIZE 100000

#define TRAIN_SIZE 697882 
#define I_NODE 0 // internal node
#define M_NODE 1 // match node
#define LEFT 0
#define RIGHT 1

#define MASK(x,y) (((x) & 0x000000ff) << (y))
typedef struct node{
    bool type; //I_NODE or M_NODE
    uint32_t port;
    struct node* lchild;
    struct node* rchild;
}node_t;

#define ip_bit(ip, pos) (((ip) >> (pos)) & 0x1)
typedef struct
{
    uint32_t port;
    uint32_t mask;
    uint32_t ip;
}
fw_line_t;

struct temp_poptrie_node;

typedef struct poptrie_node
{
    uint64_t child_bitmap;  // child nodes
    uint64_t inner_bitmap;  // inner port
    struct poptrie_node** child_nodes;   // max: 64
    uint32_t* ports;    // max: 63
    struct temp_poptrie_node* p;    // use for build node
}
poptrie_node_t;

struct temp_poptrie_node
{
    poptrie_node_t* node;
    node_t* helper_root_node;
    int child_nodes_cnt;
    int ports_cnt;
};

#define inner_mask(x, depth)    (((uint64_t) 1) << ((x) + (1 << (depth)))) // 0 <= x < (1 << depth), 0 <= depth <= 5, thus use 1 ~ 63
#define child_mask(x)           (((uint64_t)1) << (x)) // 0 <= x < 64

#define MAXQUE 65

typedef struct
{
    node_t* basic_node;
    int num;
    int depth;
}
que_t;

typedef struct
{
    que_t element[MAXQUE];
    int head;
    int tail;
}
que;

void create_tree(const char*);
uint32_t *lookup_tree(uint32_t *);
void create_tree_advance(const char*);
uint32_t *lookup_tree_advance(uint32_t *);

uint32_t* read_test_data(const char* lookup_file);

#endif
