# -*- mode: python; coding: utf-8 -*-
# Copyright 2019 the AAS WorldWide Telescope project
# Licensed under the MIT License.

"""General tools for working with tile pyramids.

Toasty and the AAS WorldWide Telescope support two kinds of tile pyramid
formats: the all-sky TOAST projection, and “studies” which are tile pyramids
rooted in a subset of the sky using a tangential projection. Both kinds of
tile pyramids have much in common, and this module implements their
overlapping functionality.

"""
from __future__ import absolute_import, division, print_function

__all__ = '''
depth2tiles
generate_pos
is_subtile
next_highest_power_of_2
Pos
pos_children
pos_parent
PyramidIO
'''.split()

from collections import namedtuple
import numpy as np
import os.path

Pos = namedtuple('Pos', 'n x y')


def next_highest_power_of_2(n):
    """Ugh, this implementation is so dumb.

    We also assume that we are being called in a tiling context, in which case
    numbers less than 256 should be bumped up to 256 (the number of pixels in
    a single tile).

    """
    p = 256
    while p < n:
        p *= 2
    return p


def depth2tiles(depth):
    """Return the total number of tiles in a WWT tile pyramid of depth *depth*."""
    return (4 ** (depth + 1) - 1) // 3


def is_subtile(deeper_pos, shallower_pos):
    """Determine if one tile is a child of another.

    Parameters
    ----------
    deeper_pos : Pos
      A tile position.
    shallower_pos : Pos
      A tile position that is shallower than *deeper_pos*.

    Returns
    -------
    True if *deeper_pos* represents a tile that is a child of *shallower_pos*.

    """
    if deeper_pos.n < shallower_pos.n:
        raise ValueError('deeper_pos has a lower depth than shallower_pos')

    if deeper_pos.n == shallower_pos.n:
        return deeper_pos.x == shallower_pos.x and deeper_pos.y == shallower_pos.y

    return is_subtile(pos_parent(deeper_pos)[0], shallower_pos)


def pos_parent(pos):
    """Return a tile position's parent.

    Parameters
    ----------
    pos : Pos
      A tile position.

    Returns
    -------
    parent : Pos
      The tile position that is the parent of *pos*.
    x_index : integer, 0 or 1
      The horizontal index of the child inside its parent.
    y_index : integer, 0 or 1
      The vertical index of the child inside its parent.

    """
    if pos.n < 1:
        raise ValueError('cannot take the parent of a tile position with depth < 1')

    parent = Pos(
        n = pos.n - 1,
        x = pos.x // 2,
        y = pos.y // 2
    )
    return parent, pos.x % 2, pos.y % 2


def pos_children(pos):
    """Return the children of a tile position.

    Parameters
    ----------
    pos : :class:`Pos`
      A tile position.

    Returns
    -------
    A list of four child :class:`Pos` instances. The return value is
    guaranteed to always be a list, and the order of the children will always
    be: top left, top right, bottom left, bottom right.

    """
    n, x, y = pos.n, pos.x, pos.y
    n += 1
    x *= 2
    y *= 2

    return [
        Pos(n=n, x=x,     y=y    ),
        Pos(n=n, x=x + 1, y=y    ),
        Pos(n=n, x=x,     y=y + 1),
        Pos(n=n, x=x + 1, y=y + 1),
    ]


def _postfix_pos(pos, depth):
    if pos.n > depth:
        return

    for immed_child in pos_children(pos):
        for item in _postfix_pos(immed_child, depth):
            yield item

    yield pos


def generate_pos(depth):
    """Generate a pyramid of tile positions.

    The generate proceeds in a broadly deeper-first fashion. In particular, if
    a position *p* is yielded, you can assume that its four children have been
    yielded previously, unless the depth of *p* is equal to *depth*.

    Parameters
    ----------
    depth : int
      The tile depth to recurse to.

    Yields
    ------
    pos : :class:`Pos`
      An individual position to process.

    """
    for item in _postfix_pos(Pos(0, 0, 0), depth):
        yield item


class PyramidIO(object):
    """Manage I/O on a tile pyramid."""

    def __init__(self, base_dir):
        self._base_dir = base_dir

    def tile_path(self, pos, extension='png'):
        """Get the path for a tile, creating its containing directories.

        Parameters
        ----------
        pos : Pos
          The tile to get a path for.
        extension : str, default: "png"
          The file extension to use in the path.

        Returns
        -------
        The path as a string.

        Notes
        -----
        This function does I/O itself — it creates the parent directories
        containing the tile path. It is not an error for the parent
        directories to already exist.

        """
        level = str(pos.n)
        ix = str(pos.x)
        iy = str(pos.y)

        d = os.path.join(self._base_dir, level, iy)

        # We can't use the `exist_ok` kwarg because it's not available in Python 2.
        try:
            os.makedirs(d)
        except OSError as e:
            if e.errno != 17:
                raise  # not EEXIST

        return os.path.join(d, '{}_{}.{}'.format(iy, ix, extension))

    def get_path_scheme(self):
        """Get the scheme for buiding tile paths as used in the WTML standard.

        Returns
        -------
        The naming scheme, a string resembling ``{1}/{3}/{3}_{2}``.

        Notes
        -----
        The naming scheme is currently hardcoded to be the format given above,
        but in the future other options might become available.

        """
        return '{1}/{3}/{3}_{2}'

    def read_image(self, pos, extension='png', default='none'):
        """Read an image file for the specified tile position.

        Parameters
        ----------
        pos : :class:`Pos`
          The tile position to read.
        extension : str, defaults to "png"
          The file extension to use when constructing the path to read.
        default : str, defaults to "none"
          What to do if the specified tile file does not exist. If this is
          "none", ``None`` will be returned instead of an array. If this is
          "zeros3", an array of zeros with shape ``(256, 256, 3)`` and dtype
          ``np.uint8`` will be returned. If it is "zeros4", a similar array
          of shape ``(256, 256, 4)`` will be returned. Otherwise,
          :exc:`ValueError` will be raised.

        Returns
        -------
        The image data as a numpy array, or one of the values as specified
        based on the parameter *default*. For a typical PNG image, the
        returned array will have shape ``(256, 256, 4)`` and dtype
        ``np.uint8``.

        """
        from .io import read_image

        try:
            return read_image(self.tile_path(pos, extension))
        except IOError as e:
            if e.errno != 2:
                raise  # not EEXIST

            if default == 'none':
                return None
            elif default == 'zeros3':
                return np.zeros((256, 256, 3), dtype=np.uint8)
            elif default == 'zeros4':
                return np.zeros((256, 256, 4), dtype=np.uint8)
            else:
                raise ValueError('unexpected value for "default": {!r}'.format(default))

    def write_image(self, pos, data, extension='png'):
        """Write an image file for the specified tile position.

        The conversion of the array into an image is handled by the
        :func:`toasty.io.save_png` function — see its documentation for
        specifics. Generally, *data* should be an array of shape ``(256, 256,
        3)`` and dtype ``np.uint8``.

        Parameters
        ----------
        pos : :class:`Pos`
          The tile position to write.
        data : array-like
          The image data to write.
        extension : str, defaults to "png"
          The file extension to use when constructing the path to write.

        """
        from .io import save_png
        save_png(self.tile_path(pos, extension), data)

    def read_numpy(self, pos, extension='npy', default='nan'):
        """Read a Numpy file for the specified tile position.

        Parameters
        ----------
        pos : :class:`Pos`
          The tile position to read.
        extension : str, defaults to "npy"
          The file extension to use when constructing the path to read.
        default : str, defaults to "nan"
          What to do if the specified tile file does not exist. If this is
          "none", ``None`` will be returned instead of an array. If this is
          "nan", an array of NaNs with shape ``(256, 256)`` and dtype
          ``np.double`` will be returned. Otherwise, :exc:`ValueError` will be
          raised.

        Returns
        -------
        The saved numpy array, or one of the values as specified based on the
        parameter *default*.

        """
        try:
            return np.load(self.tile_path(pos, extension))
        except IOError as e:
            if e.errno != 2:
                raise  # not EEXIST

            if default == 'none':
                return None
            elif default == 'nan':
                arr = np.empty((256, 256), dtype=np.double)
                arr.fill(np.nan)
                return arr
            else:
                raise ValueError('unexpected value for "default": {!r}'.format(default))

    def write_numpy(self, pos, data, extension='npy'):
        """Write a numpy file for the specified tile position.

        Parameters
        ----------
        pos : :class:`Pos`
          The tile position to write.
        data : array-like
          The numpy data to write.
        extension : str, defaults to "npy"
          The file extension to use when constructing the path to write.

        """
        np.save(self.tile_path(pos, extension), data)