/* Minimal build-time declarations for Pillow's unused ImagingQuantize wrapper.
 * The standalone entry point calls the same quantizers without Python's ABI. */
#ifndef SUZU_QUANT_IMAGING_H
#define SUZU_QUANT_IMAGING_H
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "ImagingUtils.h"

typedef unsigned char UINT8;
typedef int32_t INT32;
typedef uint32_t UINT32;
enum { IMAGING_MODE_L, IMAGING_MODE_P, IMAGING_MODE_RGB, IMAGING_MODE_RGBA };
typedef struct { int size, mode; UINT8 palette[1024]; } PaletteState;
typedef struct {
    int mode, xsize, ysize, mutex;
    UINT8 **image8;
    INT32 **image32;
    PaletteState *palette;
} *Imaging;
typedef int ImagingSectionCookie;
#define MUTEX_LOCK(x) ((void)(x))
#define MUTEX_UNLOCK(x) ((void)(x))
static inline void ImagingSectionEnter(ImagingSectionCookie *cookie) { (void)cookie; }
static inline void ImagingSectionLeave(ImagingSectionCookie *cookie) { (void)cookie; }
static inline Imaging ImagingError_ModeError(void) { return NULL; }
static inline Imaging ImagingError_MemoryError(void) { return NULL; }
static inline Imaging ImagingError_ValueError(const char *message) { (void)message; return NULL; }
static inline Imaging ImagingNewDirty(int mode, int width, int height) {
    (void)mode; (void)width; (void)height; return NULL;
}
#endif
