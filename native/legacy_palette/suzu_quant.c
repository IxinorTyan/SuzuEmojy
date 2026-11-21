#include "Quant.c"

/* The compressed byte identity of old GIFs depends on palette order, not
 * simply on the visible colors. Reuse the historical quantizer unchanged. */
__declspec(dllexport) int suzu_quantize(
    const uint8_t *bgra, int pixels, int with_alpha, int colors, int black_hidden,
    uint8_t *indices, uint8_t *palette_out
) {
    if (!bgra || !indices || !palette_out || pixels <= 0 || pixels > 16000000 ||
        colors < 1 || colors > 256) return 0;
    Pixel *input = (Pixel *)malloc((size_t)pixels * sizeof(Pixel));
    if (!input) return 0;
    Pixel transparent = { .v = 0 };
    int seen_transparent = 0;
    for (int i = 0; i < pixels; ++i) {
        const uint8_t *source = bgra + (size_t)i * 4;
        Pixel pixel = { .c = { source[2], source[1], source[0], with_alpha ? source[3] : 255 } };
        if (with_alpha && pixel.c.a == 0) {
            if (!seen_transparent) {
                seen_transparent = 1;
                transparent = pixel;
            }
            pixel = transparent;
        } else if (black_hidden && source[3] < 128) {
            pixel.c.r = pixel.c.g = pixel.c.b = 0;
        }
        input[i] = pixel;
    }
    Pixel *palette = NULL;
    uint32_t count = 0, *output = NULL;
    int ok = with_alpha ?
        quantize_octree(input, (uint32_t)pixels, colors, &palette, &count, &output, 1) :
        quantize(input, (uint32_t)pixels, colors, &palette, &count, &output, 0);
    free(input);
    if (ok <= 0 || count > 256) return 0;
    for (uint32_t i = 0; i < count; ++i)
        memcpy(palette_out + i * 4, palette[i].a.v, 4);
    for (int i = 0; i < pixels; ++i) indices[i] = (uint8_t)output[i];
    free(output);
    free(palette);
    return (int)count;
}
