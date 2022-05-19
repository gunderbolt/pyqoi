# Python QOI Encoder/Decoder

This is an implementation of a QOI Encoder/Decoder in Python.

QOI is the "Quite OK Image" format by Dominic Szablewski. Refer to [the QOI homepage](https://qoiformat.org/) for more information including the specification and links to example code in C.

The specification is fairly simple. My goal is to use it to create various implementations; first a procedural one, then a declarative implementation using [Construct](https://construct.readthedocs.io/en/latest/intro.html).


## Interface

`qoi_encode` and `qoi_decode` functions are developed for each implementation.


### qoi_encode

```python
def qoi_encode(
        width: int,
        height: int,
        data: bytes,
        channels=CHANNELS_RGB,
        colorspace=COLORSPACE_SRGB_WITH_LINEAR_ALPHA) -> bytes:
    ...

```

`width` and `height` are the width and height, respectively, of the image to encode. These are used in the qoi header and the encoder uses these to know how much data is expected.

`data` is the image data. For `channels==CHANNELS_RGBA` (or 4), each pixel is broken down into its red, green, blue, and alpha values (each 0-255, 1 byte). `channels==CHANNELS_RGB` does not include alpha values and internally uses 255 for them.

`colorspace` is only included in the qoi header.

Return value is the data exactly as it should appear in the resulting qoi file.


### qoi_decode

```python
def qoi_decode(data: bytes) -> bytes:
    ...
```

`data` is the data exactly as it would appear in the qoi file. For example, decoding a 4-channel image in `test.qoi` could be done as follows:

```python
with open('test.qoi', 'rb') as f:
    rgba_data = qoi_decode(f.read())
```

Return value is the 3 or 4 channel pixel data.


## Procedural Implementation Issues

- `qoi_encode` assumes four channels.
- `qoi_encode` doesn't check width, height, channels, colorspace for invalid entries.


## Future / Ideas

- Create functions that work with streaming pixel/qoi data.
- Create implementation using [Construct](https://construct.readthedocs.io/en/latest/intro.html).
