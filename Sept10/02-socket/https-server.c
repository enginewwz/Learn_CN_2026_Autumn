#include <errno.h>
#include <unistd.h>
#include <malloc.h>
#include <string.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/epoll.h>
#include <netinet/in.h>
#include <resolv.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <signal.h>
#include "openssl/ssl.h"
#include "openssl/err.h"

enum state
{
	LISTENER, HANDSHAKE, READING, WRITING, CLOSING
};

struct connection
{
	int fd, tls;
	SSL *ssl;
	enum state state;
	char input[8192];
	size_t used, sent;
};

static void die(const char *what)
{
	perror(what);
	exit(EXIT_FAILURE);
}

// nonblock fd
static int nonblock(int fd)
{
	int flags = fcntl(fd, F_GETFL, 0);
	return flags < 0 ? -1 : fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

static int make_listener(int port)
{
	// init socket, listening to port
	int fd = socket(AF_INET, SOCK_STREAM, 0);	// sockfd
	if (fd < 0)
		die("socket");
	int enable = 1;
	if (setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &enable, sizeof(int)) < 0)
		die("setsockopt(SO_REUSEADDR)");

	struct sockaddr_in addr;
	bzero(&addr, sizeof(addr));
	addr.sin_family = AF_INET;
	addr.sin_addr.s_addr = INADDR_ANY;
	addr.sin_port = htons(port);

	// nonblock fd
	if (nonblock(fd) < 0)
		die("nonblock");
	if (bind(fd, (struct sockaddr*)&addr, sizeof(addr)) < 0)
		die("bind");
	if (listen(fd, 128) < 0)
		die("listen");
	return fd;
}

static int watch(int ep, int op, struct connection *c, unsigned events)
{
	struct epoll_event ev =
	{
		.events = events, .data.ptr = c
	};
	return epoll_ctl(ep, op, c->fd, &ev);
}

static void drop(int ep, struct connection *c)
{
	epoll_ctl(ep, EPOLL_CTL_DEL, c->fd, NULL);
	if (c->ssl)
		SSL_free(c->ssl);
	close(c->fd);
	free(c);
}

/* Return 0 when waiting is arranged, -1 on a fatal error. */
static int tls_wait(int ep, struct connection *c, int ret)
{
	int error = SSL_get_error(c->ssl, ret);
	if (error == SSL_ERROR_WANT_READ)
		return watch(ep, EPOLL_CTL_MOD, c, EPOLLIN);
	if (error == SSL_ERROR_WANT_WRITE)
		return watch(ep, EPOLL_CTL_MOD, c, EPOLLOUT);
	return -1;
}

/* Return 0 to keep the connection, -1 to close it. */
static int drive(int ep, struct connection *c)
{
	static const char response[] =
		"HTTP/1.0 200 OK\r\nContent-Length: 27\r\n\r\n"
		"CNLab 2: Socket programming";
	for (;;)
	{
		int n;
		size_t room, left;
		switch (c->state)
		{
			case HANDSHAKE:
				ERR_clear_error();
				n = SSL_accept(c->ssl);
				if (n != 1)
					return tls_wait(ep, c, n);
				c->state = READING;
				break;

			case READING:
				room = sizeof(c->input) - 1 - c->used;
				if (room == 0)
					return -1;
				if (c->tls)
				{
					ERR_clear_error();
					n = SSL_read(c->ssl, c->input + c->used, (int)room);
					if (n <= 0)
						return tls_wait(ep, c, n);
				}
				else
				{
					n = recv(c->fd, c->input + c->used, room, 0);
					if (n < 0 && errno == EINTR)
						break;
					if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
						return watch(ep, EPOLL_CTL_MOD, c, EPOLLIN);
					if (n <= 0)
						return -1;
				}
				c->used += (size_t)n;
				c->input[c->used] = '\0';
				if (!strstr(c->input, "\r\n\r\n"))
					break;
				c->state = WRITING;
				break;

			case WRITING:
				left = sizeof(response) - 1 - c->sent;
				if (left == 0)
				{
					c->state = CLOSING;
					break;
				}
				if (c->tls)
				{
					ERR_clear_error();
					n = SSL_write(c->ssl, response + c->sent, (int)left);
					if (n <= 0)
						return tls_wait(ep, c, n);
				}
				else
				{
					n = send(c->fd, response + c->sent, left, MSG_NOSIGNAL);
					if (n < 0 && errno == EINTR)
						break;
					if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
						return watch(ep, EPOLL_CTL_MOD, c, EPOLLOUT);
					if (n <= 0)
						return -1;
				}
				c->sent += (size_t)n;
				break;

			case CLOSING:
				if (c->tls)
				{
					ERR_clear_error();
					n = SSL_shutdown(c->ssl);
					if (n < 0)
						return tls_wait(ep, c, n);
					/* n == 0: our close_notify was sent; do not await peer. */
				}
				return -1;

			default:
				return -1;
		}
	}
}

static void accept_clients(int ep, struct connection *listener, SSL_CTX *ctx)
{
	/* LT will notify again if more than 64 connections are queued. */
	for (int i = 0; i < 64; ++i)
	{
		int fd = accept(listener->fd, NULL, NULL);
		if (fd < 0)
		{
			if (errno == EINTR)
				continue;
			if (errno != EAGAIN && errno != EWOULDBLOCK)
				perror("accept");
			return;
		}
		if (nonblock(fd) < 0)
		{
			close(fd);
			continue;
		}
		struct connection *c = calloc(1, sizeof(*c));
		if (!c)
		{
			close(fd);
			continue;
		}
		c->fd = fd;
		c->tls = listener->tls;
		c->state = c->tls ? HANDSHAKE : READING;
		if (c->tls)
		{
			c->ssl = SSL_new(ctx);
			if (!c->ssl || SSL_set_fd(c->ssl, fd) != 1)
			{
				if (c->ssl)
					SSL_free(c->ssl);
				close(fd);
				free(c);
				continue;
			}
		}
		if (watch(ep, EPOLL_CTL_ADD, c, EPOLLIN) < 0)
		{
			drop(ep, c);
			continue;
		}
		if (drive(ep, c) < 0)
			drop(ep, c);
	}
}

int main(void)
{
	signal(SIGPIPE, SIG_IGN);

	// init SSL Library
	SSL_library_init();
	OpenSSL_add_all_algorithms();
	SSL_load_error_strings();

	// enable TLS method
	const SSL_METHOD *method = TLS_server_method();
	SSL_CTX *ctx = SSL_CTX_new(method);
	if (!ctx)
	{
		ERR_print_errors_fp(stderr);
		return EXIT_FAILURE;
	}

	// load certificate and private key
	if (SSL_CTX_use_certificate_file(ctx, "./keys/cnlab.cert", SSL_FILETYPE_PEM) <= 0)
	{
		ERR_print_errors_fp(stderr);
		SSL_CTX_free(ctx);
		return EXIT_FAILURE;
	}
	if (SSL_CTX_use_PrivateKey_file(ctx, "./keys/cnlab.prikey", SSL_FILETYPE_PEM) <= 0)
	{
		ERR_print_errors_fp(stderr);
		SSL_CTX_free(ctx);
		return EXIT_FAILURE;
	}
	if (SSL_CTX_check_private_key(ctx) != 1)
	{
		ERR_print_errors_fp(stderr);
		SSL_CTX_free(ctx);
		return EXIT_FAILURE;
	}

	int ep = epoll_create1(EPOLL_CLOEXEC);
	if (ep < 0)
		die("epoll_create1");

	// Both listeners remain alive for the entire event loop.
	struct connection http =
	{
		.fd = make_listener(80), .tls = 0, .state = LISTENER
	};
	struct connection https =
	{
		.fd = make_listener(443), .tls = 1, .state = LISTENER
	};
	if (watch(ep, EPOLL_CTL_ADD, &http, EPOLLIN) < 0 ||
		watch(ep, EPOLL_CTL_ADD, &https, EPOLLIN) < 0)
		die("epoll_ctl: listener");

	struct epoll_event events[64];
	while (1)
	{
		int count = epoll_wait(ep, events, 64, -1);
		if (count < 0)
		{
			if (errno == EINTR)
				continue;
			die("epoll_wait");
		}
		for (int i = 0; i < count; ++i)
		{
			struct connection *c = events[i].data.ptr;
			if (c->state == LISTENER)
				accept_clients(ep, c, ctx);
			else if (drive(ep, c) < 0)
				drop(ep, c);
		}
	}
}
