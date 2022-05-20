from itertools import repeat, chain, compress, cycle
from struct import pack, unpack_from
from typing import Generator
import warnings


CHANNELS_RGB = 3
CHANNELS_RGBA = 4
COLORSPACE_SRGB_WITH_LINEAR_ALPHA = 0
COLORSPACE_ALL_CHANNELS_LINEAR = 1

TAG_QOI_OP_RGB = 0xfe
TAG_QOI_OP_RGBA = 0xff
TAG_QOI_OP_INDEX = 0b00 << 6
TAG_QOI_OP_DIFF = 0b01 << 6
TAG_QOI_OP_LUMA = 0b10 << 6
TAG_QOI_OP_RUN = 0b11 << 6

TAG_QOI_2B_MASK = 0xc0


qoi_index_position = lambda r, g, b, a: (r*3 + g*5 + b*7 + a*11) % 64
s8_arith = lambda x: ((128+x) % 256) - 128
u8_arith = lambda x: x % 256


def pixels(data: bytes, channels: int) -> Generator[tuple[int, int, int, int], None, None]:
    data_iter = iter(data)
    try:
        while True:
            yield (
                next(data_iter),
                next(data_iter),
                next(data_iter),
                255 if channels==CHANNELS_RGB else next(data_iter)
            )
    except StopIteration:
        pass


def qoi_encode(
        width: int,
        height: int,
        data: bytes,
        channels=CHANNELS_RGB,
        colorspace=COLORSPACE_SRGB_WITH_LINEAR_ALPHA) -> bytes:
    TOTAL_PIXELS = width*height
    prev_pixel = (0, 0, 0, 255)
    indexed_pixels = [(0, 0, 0, 0)] * 64

    if width <= 0:
        raise ValueError('width must be larger than 0.')
    if height <= 0:
        raise ValueError('height must be larger than 0.')
    if channels not in (CHANNELS_RGB, CHANNELS_RGBA):
        raise ValueError(f'channels must be CHANNELS_RGB ({CHANNELS_RGB}) or CHANNELS_RGBA ({CHANNELS_RGBA}).')
    if colorspace not in (COLORSPACE_SRGB_WITH_LINEAR_ALPHA, COLORSPACE_ALL_CHANNELS_LINEAR):
        raise ValueError(f'colorspace must be COLORSPACE_SRGB_WITH_LINEAR_ALPHA ({COLORSPACE_SRGB_WITH_LINEAR_ALPHA}) or COLORSPACE_ALL_CHANNELS_LINEAR ({COLORSPACE_ALL_CHANNELS_LINEAR}).')
    if len(data) < (TOTAL_PIXELS * channels):
        raise ValueError(f'data is too short. Exp: {TOTAL_PIXELS*channels}, Act: {len(data)}')

    output = []
    output.append(pack('>4sIIBB', b'qoif', width, height, channels, colorspace))

    run_count = 0
    for pixel in pixels(data, channels):
        if pixel == prev_pixel:
            run_count += 1
            if run_count == 62:
                output.append(pack('>B', TAG_QOI_OP_RUN | 61))  # 62 bias -1 => 61
                run_count = 0
        else:
            if run_count > 0:
                output.append(pack('>B', TAG_QOI_OP_RUN | (run_count-1)))  # bias -1
                run_count = 0
                # Continue to the next OP check because we have updated pixel

            prev_index = qoi_index_position(*pixel)
            if pixel == indexed_pixels[prev_index]:
                # QOI_OP_INDEX
                output.append(pack('>B', TAG_QOI_OP_INDEX | prev_index))
            elif prev_pixel[3] != pixel[3]:
                # QOI_OP_RGBA - it is the only option left if alpha is different
                output.append(pack('>BBBBB', TAG_QOI_OP_RGBA, *pixel))
                indexed_pixels[prev_index] = pixel
            else:
                dr = s8_arith(pixel[0] - prev_pixel[0])
                dg = s8_arith(pixel[1] - prev_pixel[1])
                db = s8_arith(pixel[2] - prev_pixel[2])

                dr_dg = s8_arith(dr - dg)
                db_dg = s8_arith(db - dg)

                if ((-2<=dr<=1) and (-2<=dg<=1) and (-2<=db<=1)):
                    # QOI_OP_DIFF
                    output.append(pack('>B', TAG_QOI_OP_DIFF | (dr+2)<<4 | (dg+2)<<2 | (db+2)))
                    indexed_pixels[prev_index] = pixel
                elif ((-32<=dg<=31) and (-8<=dr_dg<=7) and (-8<=db_dg<=7)):
                    # QOI_OP_LUMA
                    output.append(pack('>BB', TAG_QOI_OP_LUMA | dg+32, (dr_dg+8)<<4 | (db_dg+8)))
                    indexed_pixels[prev_index] = pixel
                else:
                    # QOI_OP_RGB
                    output.append(pack('>BBBB', TAG_QOI_OP_RGB, *pixel[:3]))
                    indexed_pixels[prev_index] = pixel

            prev_pixel = pixel

    # Check the run_count one more time in case it was the final set of pixels
    if run_count > 0:
        output.append(pack('>B', TAG_QOI_OP_RUN | (run_count-1)))  # bias -1

    output.append(b'\x00'*7 + b'\x01')

    return b''.join(output)


def qoi_decode(data: bytes) -> bytes:
    output = []
    header = unpack_from('>4sIIBB', data, 0)
    (footer,) = unpack_from('8s', data, len(data)-8)
    (magic, width, height, channels, colorspace) = header
    if magic != b'qoif':
        raise ValueError('qoi file invalid!')
    if footer != b'\x00\x00\x00\x00\x00\x00\x00\x01':
        warnings.warn(f'data footer invalid! ({footer!r})', RuntimeWarning)

    if channels not in (3, 4):
        raise ValueError(f'Encoded channels invalid! ({channels})')

    data_iter = iter(data[14:-8])  # The slice excludes the header and footer
    indexed_pixels = [(0, 0, 0, 0)] * 64
    pixel = (0, 0, 0, 255)
    for tag in data_iter:
        prev_pixel = pixel
        if tag == TAG_QOI_OP_RGB:
            pixel = (
                next(data_iter),
                next(data_iter),
                next(data_iter),
                prev_pixel[3],
            )
        elif tag == TAG_QOI_OP_RGBA:
            pixel = (
                next(data_iter),
                next(data_iter),
                next(data_iter),
                next(data_iter),
            )
        elif (tag & TAG_QOI_2B_MASK) == TAG_QOI_OP_INDEX:
            index = tag & 0x3f
            pixel = indexed_pixels[index]
        elif (tag & TAG_QOI_2B_MASK) == TAG_QOI_OP_DIFF:
            pixel = (
                u8_arith(prev_pixel[0] + ((tag >> 4) & 0x3) - 2),
                u8_arith(prev_pixel[1] + ((tag >> 2) & 0x3) - 2),
                u8_arith(prev_pixel[2] + (tag & 0x3) - 2),
                prev_pixel[3],
            )
        elif (tag & TAG_QOI_2B_MASK) == TAG_QOI_OP_LUMA:
            dg = (tag & 0x3f) - 32

            diffs = next(data_iter)
            dr_dg = ((diffs >> 4) & 0xf) - 8
            db_dg = (diffs & 0xf) - 8

            pixel = (
                u8_arith(prev_pixel[0] + dr_dg + dg),
                u8_arith(prev_pixel[1] + dg),
                u8_arith(prev_pixel[2] + db_dg + dg),
                prev_pixel[3],
            )
        else:  # TAG_QOI_OP_RUN: it must be this option; no need to check
            pixel = prev_pixel
            run_len_biased = tag & 0x3f
            # run_len_biased has bias of -1.
            # The last pixel will be stored after block.
            output.extend(repeat(pixel, run_len_biased))

        output.append(pixel)
        indexed_pixels[qoi_index_position(*pixel)] = pixel

    if channels == CHANNELS_RGBA:
        return bytes(chain.from_iterable(output))
    # 3 Channels: take every value except the alpha.
    return bytes(compress(chain.from_iterable(output), cycle((1, 1, 1, 0))))


if __name__ == '__main__':
    # Simple Tests

    # 4 channel encode/decode test.
    tw = 4
    th = 3
    tch = CHANNELS_RGBA
    tcs = COLORSPACE_SRGB_WITH_LINEAR_ALPHA  # NOT REALLY USED.

    # A test image that should use all the OPs at least once. It doesn't fully
    # test them, however!
    data_tups = (
        # DIFF -1 -1 -1 (idx 38), Run 4,
        (255, 255, 255, 255), (255, 255, 255, 255), (255, 255, 255, 255), (255, 255, 255, 255), 
        # Run 4 Cont., DIFF 1 1 1 (idx 53), RGBA (idx 48), INDEX (idx 53),
        (255, 255, 255, 255), (0, 0, 0, 255), (0, 255, 0, 127), (0, 0, 0, 255),
        # LUMA (idx 61), INDEX (idx 48), RUN 1, RGB (idx 38),
        (252, 250, 254, 255), (0, 255, 0, 127), (0, 255, 0, 127), (127, 127, 255, 127)
    )
    data = bytes(chain.from_iterable(data_tups))

    qoi_out = qoi_encode(tw, th, data, tch, tcs)
    with open('test_out_4ch.qoi', 'wb') as f:
        f.write(qoi_out)

    decode_data = qoi_decode(qoi_out)
    print(f'decode_data == data? {decode_data == data}')

    # 3 channel encode/decode test.
    tw = 3
    th = 3
    tch = CHANNELS_RGB
    tcs = COLORSPACE_SRGB_WITH_LINEAR_ALPHA  # NOT REALLY USED.

    data_tups = (
        # RGB, RGB, RGB,
        (255, 0, 0), (255, 255, 255), (0, 0, 255),
        # DIFF, DIFF, LUMA,
        (0, 0, 0), (1, 1, 1), (5, 5, 5),
        # INDEX, Run 2,
        (255, 0, 0), (255, 0, 0), (255, 0, 0),
    )
    data = bytes(chain.from_iterable(data_tups))
    qoi_out = qoi_encode(tw, th, data, tch, tcs)
    with open('test_out_3ch.qoi', 'wb') as f:
        f.write(qoi_out)

    decode_data = qoi_decode(qoi_out)
    print(f'decode_data == data? {decode_data == data}')