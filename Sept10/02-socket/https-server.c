#include <errno.h>
#include <inttypes.h>
#include <strings.h>
#include <sys/stat.h>
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

#define HTTPS_ORIGIN "https://10.0.0.1"

enum state
{
	LISTENER, HANDSHAKE, READING, WRITING, CLOSING
};

struct http_request
{
	char *method;
	char *target;
	char *range;
	int if_range;
};

struct connection
{
	int fd, tls;
	SSL *ssl;
	enum state state;
	char input[4096];
	char output[8192];
	size_t used, sent, output_len;
	/* output holds the response header, then successive file chunks. */
	int file_fd;
	off_t file_offset, file_remaining;
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
	addr.sin_addr.s_addr = htonl(INADDR_ANY);
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
	if (c->file_fd >= 0)
		close(c->file_fd);
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

static int is_token(const char *text)
{
	if (!*text)
		return 0;
	for (; *text; ++text)
	{
		unsigned char ch = (unsigned char)*text;
		if (!((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') ||
			(ch >= '0' && ch <= '9') || strchr("!#$%&'*+-.^_`|~", ch)))
			return 0;
	}
	return 1;
}

/* Split the complete header in place. Pointers remain valid in c->input. */
static int parse_request(struct connection *c, struct http_request *request)
{
	char *end = strstr(c->input, "\r\n\r\n");
	if (!end || memchr(c->input, '\0', (size_t)(end + 4 - c->input)))	// \r\n\r\n needs 4 bytes
		return -1;

	char *line_end = strstr(c->input, "\r\n");
	*line_end = '\0';	// cut into split str
	request->method = c->input;
	char *space = strchr(request->method, ' ');
	if (!space)
		return -1;
	*space = '\0';
	request->target = space + 1;
	space = strchr(request->target, ' ');
	if (!space)
		return -1;
	*space = '\0';
	char *version = space + 1;
	if (!is_token(request->method) || !*request->target ||
		(strcmp(version, "HTTP/1.0") != 0 && strcmp(version, "HTTP/1.1") != 0))
		return -1;
	for (char *p = request->target; *p; ++p)
		if ((unsigned char)*p <= 32 || (unsigned char)*p >= 127 || *p == '#')
			return -1;

	int host_seen = 0, range_seen = 0;
	for (char *line = line_end + 2; line < end; line = line_end + 2)
	{
		line_end = strstr(line, "\r\n");
		if (!line_end)
			return -1;
		*line_end = '\0';
		char *colon = strchr(line, ':');
		if (!colon)
			return -1;
		*colon = '\0';
		if (!is_token(line))
			return -1;
		char *value = colon + 1;
		while (*value == ' ' || *value == '\t')
			++value;
		char *tail = line_end;
		while (tail > value && (tail[-1] == ' ' || tail[-1] == '\t'))
			*--tail = '\0';
		for (char *p = value; *p; ++p)
			if (((unsigned char)*p < 32 && *p != '\t') || (unsigned char)*p == 127)	// 127: del; > 127 ex. 中文
				return -1;

		if (strcasecmp(line, "Host") == 0)
		{
			if (host_seen++ || !*value)
				return -1;
		}
		else if (strcasecmp(line, "Range") == 0)
		{
			++range_seen;
			request->range = value;
		}
		else if (strcasecmp(line, "If-Range") == 0)
			request->if_range = 1;		// check fingerprint
	}
	if (strcmp(version, "HTTP/1.1") == 0 && !host_seen)
		return -1;
	// Multiple ranges and unverified If-Range conditions use a full 200 response.
	if (range_seen > 1 || request->if_range)
		request->range = NULL;
	return 0;
}

/* Responses without a body: redirect, missing file, or invalid request. */
static int empty_response(struct connection *c, int status, const char *reason,
	const char *extra_headers)
{
	int n = snprintf(c->output, sizeof(c->output),
		"HTTP/1.1 %d %s\r\n"
		"%s"
		"Content-Length: 0\r\n"
		"Connection: close\r\n\r\n",
		status, reason, extra_headers);
	if (n < 0 || (size_t)n >= sizeof(c->output))
		return -1;
	c->output_len = (size_t)n;
	c->sent = 0;
	return 0;
}

static int hex_value(char ch)
{
	if (ch >= '0' && ch <= '9')
		return ch - '0';
	if (ch >= 'a' && ch <= 'f')
		return ch - 'a' + 10;
	if (ch >= 'A' && ch <= 'F')
		return ch - 'A' + 10;
	return -1;
}

/* Decode only the URL path; query parameters are not part of a filename. */
static int open_local_file(const char *target)
{
	if (target[0] != '/')
		return -1;
	char path[4096];
	size_t length = strcspn(target, "?");
	size_t used = 0;
	for (size_t i = 1; i < length; ++i)	// path check & solve % issue
	{
		unsigned char ch = (unsigned char)target[i];
		if (ch == '%')
		{
			if (i + 2 >= length)
				return -1;
			int high = hex_value(target[i + 1]), low = hex_value(target[i + 2]);
			if (high < 0 || low < 0)
				return -1;
			ch = (unsigned char)(high * 16 + low);
			i += 2;
		}
		if (ch < 32 || ch == 127 || ch == '\\' || used + 1 >= sizeof(path))
			return -1;
		path[used++] = (char)ch;
	}
	path[used] = '\0';
	if (used == 0)
		strcpy(path, "index.html");
	else if (path[used - 1] == '/')	// not a file
		return -1;

	/* Walk below cwd; reject parent traversal and symbolic links. */
	int fd = open(".", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	if (fd < 0)
		return -1;
	char *save = NULL;
	for (char *part = strtok_r(path, "/", &save); part; )	// TOCTOU-safe
	{
		if (strcmp(part, "..") == 0)
		{
			close(fd);
			return -1;
		}
		char *next = strtok_r(NULL, "/", &save);
		int flags = O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK;
		if (next)
			flags |= O_DIRECTORY;
		int child = openat(fd, part, flags);
		close(fd);
		if (child < 0)
			return -1;
		fd = child;
		part = next;
	}
	return fd;
}

/* Saturate huge numbers rather than allowing integer overflow. */
static int parse_decimal(const char **cursor, uintmax_t *value)
{
	const char *p = *cursor;
	if (*p < '0' || *p > '9')
		return -1;
	*value = 0;
	while (*p >= '0' && *p <= '9')
	{
		unsigned digit = (unsigned)(*p++ - '0');
		if (*value > (UINTMAX_MAX - digit) / 10)
			*value = UINTMAX_MAX;		// no break for handle cursor
		else
			*value = *value * 10 + digit;
	}
	*cursor = p;
	return 0;
}

/* 0: ignore Range and send 200; 1: send 206; -1: unsatisfiable, send 416. */
static int select_range(const char *range, uintmax_t size,
	uintmax_t *first, uintmax_t *length)
{
	*first = 0;
	*length = size;
	if (!range || strncasecmp(range, "bytes=", 6) != 0 || strchr(range, ','))
		return 0;
	const char *p = range + 6;
	uintmax_t start, end;
	if (*p == '-')
	{
		++p;
		if (parse_decimal(&p, &end) < 0 || *p)	// val stored in end
			return 0;
		if (!end || !size)
			return -1;
		*length = end < size ? end : size;
		*first = size - *length;
		return 1;
	}
	if (parse_decimal(&p, &start) < 0 || *p != '-')
		return 0;
	++p;
	end = UINTMAX_MAX;
	if (*p && (parse_decimal(&p, &end) < 0 || *p))
		return 0;
	if (end < start)
		return 0;
	if (start >= size)
		return -1;
	if (end >= size)
		end = size - 1;
	*first = start;
	*length = end - start + 1;
	return 1;
}

// MIME match
static const char *content_type(const char *target)
{
	size_t length = strcspn(target, "?");
	if ((length == 1 && target[0] == '/') ||
		(length >= 5 && strncasecmp(target + length - 5, ".html", 5) == 0) ||
		(length >= 4 && strncasecmp(target + length - 4, ".htm", 4) == 0))
		return "text/html";
	if (length >= 4 && strncasecmp(target + length - 4, ".txt", 4) == 0)
		return "text/plain";
	return "application/octet-stream";
}

static int build_response(struct connection *c)
{
	struct http_request request = {0};
	if (parse_request(c, &request) < 0)
		return empty_response(c, 400, "Bad Request", "");

	if (!c->tls)
	{
		/* Every HTTP method redirects. Keep the path and query, not the body. */
		const char *target = request.target;
		if (strncmp(target, "http://", 7) == 0)
			target = strpbrk(target + 7, "/?");
		else if (strncmp(target, "https://", 8) == 0)
			target = strpbrk(target + 8, "/?");
		if (!target || (*target != '/' && *target != '?'))
			target = "/";
		char location[sizeof(c->input) + sizeof(HTTPS_ORIGIN) + 32];
		int n = snprintf(location, sizeof(location), "Location: %s%s%s\r\n",
			HTTPS_ORIGIN, *target == '?' ? "/" : "", target);
		if (n < 0 || (size_t)n >= sizeof(location))
			return -1;
		return empty_response(c, 301, "Moved Permanently", location);
	}

	if (strcmp(request.method, "GET") != 0)
		return empty_response(c, 405, "Method Not Allowed", "Allow: GET\r\n");

	c->file_fd = open_local_file(request.target);
	struct stat info;
	if (c->file_fd < 0 || fstat(c->file_fd, &info) < 0 ||
		!S_ISREG(info.st_mode) || info.st_size < 0)
		return empty_response(c, 404, "Not Found", "");

	uintmax_t first, length, size = (uintmax_t)info.st_size;
	int partial = select_range(request.range, size, &first, &length);
	char range_header[160] = "";
	if (partial < 0)
	{
		snprintf(range_header, sizeof(range_header),
			"Content-Range: bytes */%" PRIuMAX "\r\n", size);
		return empty_response(c, 416, "Range Not Satisfiable", range_header);
	}
	if (partial)
		snprintf(range_header, sizeof(range_header),
			"Content-Range: bytes %" PRIuMAX "-%" PRIuMAX "/%" PRIuMAX "\r\n",
			first, first + length - 1, size);

	int n = snprintf(c->output, sizeof(c->output),
		"HTTP/1.1 %s\r\n"
		"Content-Length: %" PRIuMAX "\r\n"
		"Content-Type: %s\r\n"
		"Accept-Ranges: bytes\r\n"
		"%s"
		"Connection: close\r\n\r\n",
		partial ? "206 Partial Content" : "200 OK",
		length, content_type(request.target), range_header);
	if (n < 0 || (size_t)n >= sizeof(c->output))
		return -1;
	c->output_len = (size_t)n;
	c->sent = 0;
	c->file_offset = (off_t)first;
	c->file_remaining = (off_t)length;
	return 0;
}

/* Reuse output only after the previous header/file chunk was fully sent. */
static int read_file_chunk(struct connection *c)	// config file occupy more than buffer issue
{
	size_t count = sizeof(c->output);
	if (c->file_remaining < (off_t)count)
		count = (size_t)c->file_remaining;
	ssize_t n;
	do
	{
		n = pread(c->file_fd, c->output, count, c->file_offset);
	} while (n < 0 && errno == EINTR);
	if (n <= 0)
		return -1;
	c->file_offset += n;
	c->file_remaining -= n;
	c->output_len = (size_t)n;
	c->sent = 0;
	return 0;
}

/* Return 0 to keep the connection, -1 to close it. */
static int drive(int ep, struct connection *c)
{
	size_t written = 0;
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
				{
					if (empty_response(c, 431, "Request Header Fields Too Large", "") < 0)
						return -1;
					c->state = WRITING;
					break;
				}
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
				if (build_response(c) < 0)
					return -1;
				c->state = WRITING;
				break;

			case WRITING:
				left = c->output_len - c->sent;
				if (left == 0)
				{
					if (c->file_remaining == 0)
						c->state = CLOSING;
					else if (read_file_chunk(c) < 0)
						return -1;
					break;
				}
				if (c->tls)
				{
					ERR_clear_error();
					n = SSL_write(c->ssl, c->output + c->sent, (int)left);
					if (n <= 0)
						return tls_wait(ep, c, n);
				}
				else
				{
					n = send(c->fd, c->output + c->sent, left, MSG_NOSIGNAL);
					if (n < 0 && errno == EINTR)
						break;
					if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
						return watch(ep, EPOLL_CTL_MOD, c, EPOLLOUT);
					if (n <= 0)
						return -1;
				}
				c->sent += (size_t)n;
				written += (size_t)n;
				/* Let other ready connections run during large downloads. */
				if (written >= 64 * 1024)
					return watch(ep, EPOLL_CTL_MOD, c, EPOLLOUT);
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
		c->file_fd = -1;
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
