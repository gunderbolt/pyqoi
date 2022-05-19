from itertools import repeat, chain, compress, cycle
from struct import pack, unpack_from
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


def qoi_encode(
        width: int,
        height: int,
        data: bytes,
        channels=CHANNELS_RGB,
        colorspace=COLORSPACE_SRGB_WITH_LINEAR_ALPHA) -> bytes:
    next_pixel = (0, 0, 0, 255)
    pixel_index = 0
    TOTAL_PIXELS = width*height
    indexed_pixels = [(0, 0, 0, 0)] * 64
    final_pixel = False

    output = []
    output.append(b'qoif')
    output.append(pack('>I', width))
    output.append(pack('>I', height))
    output.append(pack('>B', channels))
    output.append(pack('>B', colorspace))

    while pixel_index < TOTAL_PIXELS:
        prev_pixel = next_pixel
        # QOI_OP_RUN
        try:
            run_count = -1
            while next_pixel == prev_pixel:
                next_pixel = tuple(data[pixel_index*4:pixel_index*4+4])
                pixel_index += 1
                run_count += 1
        except IndexError:
            final_pixel = True
        if run_count > 0:
            while run_count > 62:
                output.append(pack('>B', TAG_QOI_OP_RUN | 61))  # 62 bias -1 => 61
                run_count -= 62
            output.append(pack('>B', TAG_QOI_OP_RUN | (run_count-1)))  # bias -1
            if final_pixel:
                break
            # Continue to the next OP check because we have updated next_pixel

        # QOI_OP_INDEX
        prev_index = qoi_index_position(*next_pixel)
        if next_pixel == indexed_pixels[prev_index]:
            output.append(pack('>B', TAG_QOI_OP_INDEX | prev_index))
            continue

        # QOI_OP_RGBA - it is the only option left if alpha is different
        if prev_pixel[3] != next_pixel[3]:
            output.append(pack('>BBBBB', TAG_QOI_OP_RGBA, *next_pixel))
            indexed_pixels[prev_index] = next_pixel
            continue

        # QOI_OP_DIFF
        dr = s8_arith(next_pixel[0] - prev_pixel[0])
        dg = s8_arith(next_pixel[1] - prev_pixel[1])
        db = s8_arith(next_pixel[2] - prev_pixel[2])
        if ((-2<=dr<=1) and (-2<=dg<=1) and (-2<=db<=1)):
            output.append(pack('>B', TAG_QOI_OP_DIFF | (dr+2)<<4 | (dg+2)<<2 | (db+2)))
            indexed_pixels[prev_index] = next_pixel
            continue

        # QOI_OP_LUMA
        dr_dg = s8_arith(dr - dg)
        db_dg = s8_arith(db - dg)
        if ((-32<=dg<=31) and (-8<=dr_dg<=7) and (-8<=db_dg<=7)):
            output.append(pack('>BB', TAG_QOI_OP_LUMA | dg+32, (dr_dg+8)<<4 | (db_dg+8)))
            indexed_pixels[prev_index] = next_pixel
            continue

        # QOI_OP_RGB
        output.append(pack('>BBBB', TAG_QOI_OP_RGB, *next_pixel[:3]))
        indexed_pixels[prev_index] = next_pixel

    output.append(b'\x00'*7 + b'\x01')

    return b''.join(output)


def qoi_decode(data: bytes) -> bytes:
    output = []
    header = unpack_from('>4sIIBB', data, 0)
    footer = unpack_from('8s', data, len(data)-8)
    (magic, width, height, channels, colorspace) = header
    if magic != b'qoif':
        raise ValueError('qoi file invalid!')
    if footer != b'\x00\x00\x00\x00\x00\x00\x00\x01':
        warnings.warn(f'data footer invalid! ({footer!r})', RuntimeWarning)

    if channels not in (3, 4):
        raise ValueError(f'Encoded channels invalid! ({channels})')

    data_iter = iter(data[14:-8])
    processed_pixels = 0
    TOTAL_PIXELS = width * height
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
        elif (tag & TAG_QOI_2B_MASK) == TAG_QOI_OP_RUN:
            pixel = prev_pixel
            run_len_biased = tag & 0x3f
            # run_len_biased has bias of -1.
            # The last pixel will be stored after block.
            output.extend(repeat(pixel, run_len_biased))
            processed_pixels += run_len_biased

        output.append(pixel)
        indexed_pixels[qoi_index_position(*pixel)] = pixel
        processed_pixels += 1

    if channels == 4:
        return bytes(chain.from_iterable(output))
    # 3 Channels: take every value except the alpha.
    return bytes(compress(chain.from_iterable(output), cycle((1, 1, 1, 0))))


if __name__ == '__main__':
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
    with open('test_out.qoi', 'wb') as f:
        f.write(qoi_out)

    decode_data = qoi_decode(qoi_out)

    print(f'decode_data == data? {decode_data == data}')
