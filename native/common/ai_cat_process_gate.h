#ifndef AI_CAT_PROCESS_GATE_H
#define AI_CAT_PROCESS_GATE_H

/* Linux-only, header-only cooperative maintenance gate. The coordinator creates
 * the directory/files. Native consumers must never create, replace, unlock or
 * repair them. A lease is an independently opened work.lock FD, closed only
 * after the actual work (including children and cleanup) has completed. */
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#ifndef PATH_MAX
#define PATH_MAX 4096
#endif

struct ai_cat_process_gate {
    bool enabled;
    int root_fd;
    int work_fd;
    dev_t directory_device, admission_device, work_device;
    ino_t directory_inode, admission_inode, work_inode;
    unsigned char ticket[24];
    char path[PATH_MAX];
};

static inline void ai_cat_process_gate_init(struct ai_cat_process_gate *gate)
{
    memset(gate, 0, sizeof(*gate));
    gate->root_fd = gate->work_fd = -1;
}

static inline void ai_cat_process_gate_close(struct ai_cat_process_gate *gate)
{
    if (gate->work_fd >= 0) close(gate->work_fd);
    if (gate->root_fd >= 0) close(gate->root_fd);
    ai_cat_process_gate_init(gate);
}

static inline bool ai_cat_gate_regular(const struct stat *value)
{
    return S_ISREG(value->st_mode) && (value->st_mode & 07777) == 0600 &&
        value->st_uid == geteuid() && value->st_nlink == 1;
}

static inline int ai_cat_gate_directory(const char *path)
{
    char component[PATH_MAX];
    const char *cursor;
    int current, next;
    struct stat value;

    if (!path || path[0] != '/' || path[1] == '\0' ||
        strlen(path) >= PATH_MAX) return -1;
    current = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (current < 0) return -1;
    cursor = path + 1;
    for (;;) {
        const char *end = strchr(cursor, '/');
        size_t length = end ? (size_t)(end - cursor) : strlen(cursor);
        if (length == 0 || length >= sizeof(component)) goto invalid;
        memcpy(component, cursor, length);
        component[length] = '\0';
        if (!strcmp(component, ".") || !strcmp(component, "..")) goto invalid;
        for (size_t i = 0; i < length; ++i)
            if ((unsigned char)component[i] < 32 ||
                (unsigned char)component[i] == 127) goto invalid;
        next = openat(current, component,
            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        if (next < 0) goto invalid;
        close(current);
        current = next;
        if (!end) break;
        cursor = end + 1;
    }
    if (fstat(current, &value) != 0 || !S_ISDIR(value.st_mode) ||
        (value.st_mode & 07777) != 0700 || value.st_uid != geteuid())
        goto invalid;
    return current;
invalid:
    close(current);
    return -1;
}

static inline int ai_cat_gate_lock_file(int directory, const char *name,
    struct stat *value)
{
    int fd = openat(directory, name, O_RDWR | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    struct stat named;
    if (fd < 0) return -1;
    if (fstat(fd, value) != 0 || !ai_cat_gate_regular(value) || value->st_size != 0 ||
        fstatat(directory, name, &named, AT_SYMLINK_NOFOLLOW) != 0 ||
        !ai_cat_gate_regular(&named) || named.st_size != 0 || named.st_dev != value->st_dev ||
        named.st_ino != value->st_ino) {
        close(fd);
        return -1;
    }
    return fd;
}

/* 0 acquired, 1 coordinator busy, -1 invalid/system failure. */
static inline int ai_cat_gate_try_shared(int fd)
{
    int result;
    do { result = flock(fd, LOCK_SH | LOCK_NB); } while (result < 0 && errno == EINTR);
    if (!result) return 0;
    return errno == EWOULDBLOCK || errno == EAGAIN ? 1 : -1;
}

static inline int ai_cat_gate_state(int directory, unsigned char state[32])
{
    struct stat value;
    unsigned char extra;
    size_t done = 0;
    ssize_t count;
    int fd = openat(directory, "state", O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) return -1;
    if (fstat(fd, &value) != 0 || !ai_cat_gate_regular(&value) || value.st_size != 32)
        goto invalid;
    while (done < 32) {
        count = read(fd, state + done, 32 - done);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) goto invalid;
        done += (size_t)count;
    }
    do { count = read(fd, &extra, 1); } while (count < 0 && errno == EINTR);
    if (count != 0 || memcmp(state, "ACG1", 4) != 0 ||
        (state[28] != 'O' && state[28] != 'C') ||
        state[29] || state[30] || state[31]) goto invalid;
    close(fd);
    return 0;
invalid:
    close(fd);
    return -1;
}

static inline bool ai_cat_gate_named_inode(int directory, const char *name,
    dev_t device, ino_t inode)
{
    struct stat value;
    return fstatat(directory, name, &value, AT_SYMLINK_NOFOLLOW) == 0 &&
        ai_cat_gate_regular(&value) && value.st_size == 0 &&
        value.st_dev == device && value.st_ino == inode;
}

/* Validate the current path again: a renamed/recreated directory is not the
 * directory protected by this process's existing lock FDs. */
static inline int ai_cat_gate_current_directory(struct ai_cat_process_gate *gate)
{
    struct stat value;
    int fd = ai_cat_gate_directory(gate->path);
    if (fd < 0) return -1;
    if (fstat(fd, &value) != 0 || value.st_dev != gate->directory_device ||
        value.st_ino != gate->directory_inode) {
        close(fd);
        return -1;
    }
    return fd;
}

/* The caller initializes once and may retry enter after a busy/invalid result.
 * Environment absent is the sole legacy-disabled mode; explicit empty fails.
 * On failure there is no lease. On success check must precede new effects. */
static inline int ai_cat_process_gate_enter(struct ai_cat_process_gate *gate)
{
    const char *path = getenv("AI_CAT_PROCESS_GATE_PATH");
    unsigned char state[32];
    struct stat directory, admission, work;
    int admission_fd = -1, current_fd = -1, result = -1;

    ai_cat_process_gate_close(gate);
    if (!path) return 0;
    gate->enabled = true;
    if (!*path || strlen(path) >= sizeof(gate->path)) goto failed;
    memcpy(gate->path, path, strlen(path) + 1);
    gate->root_fd = ai_cat_gate_directory(path);
    if (gate->root_fd < 0 || fstat(gate->root_fd, &directory) != 0) goto failed;
    gate->directory_device = directory.st_dev;
    gate->directory_inode = directory.st_ino;
    admission_fd = ai_cat_gate_lock_file(gate->root_fd, "admission.lock", &admission);
    if (admission_fd < 0) goto failed;
    gate->work_fd = ai_cat_gate_lock_file(gate->root_fd, "work.lock", &work);
    if (gate->work_fd < 0) goto failed;
    result = ai_cat_gate_try_shared(admission_fd);
    if (result != 0) goto failed;
    result = -1;
    if (ai_cat_gate_state(gate->root_fd, state) != 0) goto failed;
    if (state[28] == 'C') { result = 1; goto failed; }
    result = ai_cat_gate_try_shared(gate->work_fd);
    if (result != 0) goto failed;
    result = -1;
    gate->admission_device = admission.st_dev;
    gate->admission_inode = admission.st_ino;
    gate->work_device = work.st_dev;
    gate->work_inode = work.st_ino;
    memcpy(gate->ticket, state + 4, sizeof(gate->ticket));
    current_fd = ai_cat_gate_current_directory(gate);
    if (current_fd < 0 ||
        !ai_cat_gate_named_inode(current_fd, "admission.lock", admission.st_dev, admission.st_ino) ||
        !ai_cat_gate_named_inode(current_fd, "work.lock", work.st_dev, work.st_ino)) goto failed;
    close(current_fd);
    close(admission_fd);
    return 0;
failed:
    if (current_fd >= 0) close(current_fd);
    if (admission_fd >= 0) close(admission_fd);
    ai_cat_process_gate_close(gate);
    gate->enabled = path != NULL;
    return result;
}

/* Does not release the lease, even on revocation. Required cleanup continues
 * under the original lease; only genuinely new effects are denied. */
static inline int ai_cat_process_gate_check(struct ai_cat_process_gate *gate)
{
    unsigned char state[32];
    struct stat admission, work;
    int directory, verified_directory = -1, admission_fd = -1, result = -1;
    if (!gate->enabled)
        return getenv("AI_CAT_PROCESS_GATE_PATH") == NULL ? 0 : -1;
    if (gate->work_fd < 0 || gate->root_fd < 0) return -1;
    directory = ai_cat_gate_current_directory(gate);
    if (directory < 0) return -1;
    admission_fd = ai_cat_gate_lock_file(directory, "admission.lock", &admission);
    if (admission_fd < 0 || admission.st_dev != gate->admission_device ||
        admission.st_ino != gate->admission_inode ||
        fstat(gate->work_fd, &work) != 0 || !ai_cat_gate_regular(&work) ||
        work.st_dev != gate->work_device || work.st_ino != gate->work_inode ||
        !ai_cat_gate_named_inode(directory, "work.lock", gate->work_device, gate->work_inode))
        goto done;
    result = ai_cat_gate_try_shared(admission_fd);
    if (result != 0) goto done;
    result = ai_cat_gate_state(directory, state);
    if (result != 0) goto done;
    verified_directory = ai_cat_gate_current_directory(gate);
    if (verified_directory < 0 ||
        !ai_cat_gate_named_inode(verified_directory, "admission.lock", gate->admission_device, gate->admission_inode) ||
        !ai_cat_gate_named_inode(verified_directory, "work.lock", gate->work_device, gate->work_inode)) {
        result = -1;
        goto done;
    }
    result = state[28] == 'O' && !memcmp(state + 4, gate->ticket, 24) ? 0 : 1;
done:
    if (verified_directory >= 0) close(verified_directory);
    if (admission_fd >= 0) close(admission_fd);
    close(directory);
    return result;
}

#endif
