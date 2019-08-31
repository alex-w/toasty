# Copyright 2013-2019 Chris Beaumont and the AAS WorldWide Telescope project
# Licensed under the MIT License.

"""Computations for the TOAST projection scheme and tile pyramid format.

"""
from __future__ import absolute_import, division, print_function

__all__ = '''
depth2tiles
gen_wtml
is_subtile
iter_corners
iter_tiles
minmax_tile_filter
nxy_tile_filter
Pos
Tile
toast
'''.split()

from collections import defaultdict, namedtuple
import os
import logging
import numpy as np

from ._libtoasty import subsample, mid
from .io import save_png, read_png
from .norm import normalize

level1 = [
    [np.radians(c) for c in row]
    for row in [
            [(0, -90), (90, 0), (0, 90), (180, 0)],
            [(90, 0), (0, -90), (0, 0), (0, 90)],
            [(0, 90), (0, 0), (0, -90), (270, 0)],
            [(180, 0), (0, 90), (270, 0), (0, -90)],
    ]
]

Pos = namedtuple('Pos', 'n x y')
Tile = namedtuple('Tile', 'pos corners increasing')


def depth2tiles(depth):
    """Return the total number of tiles in a TOAST pyramid of depth *depth*."""
    return (4 ** (depth + 1) - 1) // 3


def _minmax(arr):
    return min(arr), max(arr)


def minmax_tile_filter(ra_range, dec_range):
    """Returns the tile_filter function based on a ra/dec range.

    Parameters
    ----------
    ra_range, dec_range: (array)
      The ra and dec ranges to be toasted (in the form [min,max]).
    """

    def is_overlap(tile):
        c = tile[1]

        minRa,maxRa = _minmax([(x[0] + 2*np.pi) if x[0] < 0 else x[0] for x in [y for y in c if np.abs(y[1]) !=  np.pi/2]])
        minDec,maxDec = _minmax([x[1] for x in c])
        if (dec_range[0] > maxDec) or (dec_range[1] < minDec): # tile is not within dec range
            return False
        if (maxRa - minRa) > np.pi: # tile croses circle boundary
            if (ra_range[0] < maxRa) and (ra_range[1] > minRa): # tile is not within ra range
                return False
        else:
            if (ra_range[0] > maxRa) or (ra_range[1] < minRa): # tile is not within ra range
                return False

        return True

    return is_overlap


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

    return is_subtile(_parent(deeper_pos)[0], shallower_pos)


def nxy_tile_filter(layer,tx,ty):
    """Returns the tile_filter function based on a given super-tile.

    Parameters
    ----------
    layer,tx,ty: (int)
      Layer and x,y coordinates, for a tile that will serve at the "super-tile"
      such that all subtiles will be toasted/merged.
    """


    regionLoc = Pos(n=layer,x=tx,y=ty)

    def is_overlap(tile):
        tileLoc = tile[0]

        if tileLoc.n > regionLoc.n:
            return True

        return is_subtile(regionLoc,tileLoc)

    return is_overlap


def _postfix_corner(tile, depth, bottom_only, tile_filter):
    """
    Yield subtiles of a given tile, in postfix (deepest-first) order.

    Parameters
    ----------
    tile : Tile
      Parameters of the current tile.
    depth : int
      The depth to descend to.
    bottom_only : bool
      If True, only yield tiles at max_depth.
    tile_filter : callable
      A function with signature ``tile_filter(tile) -> bool`` that determines
      which tiles will be yielded; tiles for which it returns ``False`` will
      be skipped.

    """
    n = tile[0].n
    if n > depth:
        return

    if not tile_filter(tile):
        return

    for child in _div4(tile):
        for item in _postfix_corner(child, depth, bottom_only, tile_filter):
            yield item

    if n == depth or not bottom_only:
        yield tile


def _div4(tile):
    """Return the four child tiles of an input tile."""
    n, x, y = tile.pos.n, tile.pos.x, tile.pos.y
    ul, ur, lr, ll = tile.corners
    increasing = tile.increasing

    to = mid(ul, ur)
    ri = mid(ur, lr)
    bo = mid(lr, ll)
    le = mid(ll, ul)
    ce = mid(ll, ur) if increasing else mid(ul, lr)

    n += 1
    x *= 2
    y *= 2

    return [
        Tile(Pos(n=n, x=x,     y=y    ), (ul, to, ce, le), increasing),
        Tile(Pos(n=n, x=x + 1, y=y    ), (to, ur, ri, ce), increasing),
        Tile(Pos(n=n, x=x,     y=y + 1), (le, ce, bo, ll), increasing),
        Tile(Pos(n=n, x=x + 1, y=y + 1), (ce, ri, lr, bo), increasing),
    ]


def _parent(pos):
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
    parent = Pos(
        n = pos.n - 1,
        x = pos.x // 2,
        y = pos.y // 2
    )
    return parent, pos.x % 2, pos.y % 2


def iter_corners(depth, bottom_only=True, tile_filter=None):
    """Generate a pyramid of TOAST tiles in deepest-first order.

    Parameters
    ----------
    depth : int
      The tile depth to recurse to.
    bottom_only : bool
      If True, then only the lowest tiles will be yielded.
    tile_filter : callable or None (the default)
      If not None, a filter function applied to the process;
      only tiles for which ``tile_filter(tile)`` returns True
      will be yielded

    Yields
    ------
    tile : Tile
      An individual tile to process. Tiles are yield deepest-first.

    The ``n = 0`` depth is not included.

    """
    if tile_filter is None:
        tile_filter = lambda t: True

    todo = [
        Tile(Pos(n=1, x=0, y=0), level1[0], True),
        Tile(Pos(n=1, x=1, y=0), level1[1], False),
        Tile(Pos(n=1, x=1, y=1), level1[2], True),
        Tile(Pos(n=1, x=0, y=1), level1[3], False),
    ]

    for t in todo:
        for item in _postfix_corner(t, depth, bottom_only, tile_filter):
            yield item


def iter_tiles(data_sampler, depth, merge=True,
               base_level_only=False,tile_filter=None,restart_dir=None, top=0):
    """
    Create a hierarchy of toast tiles

    Parameters
    ----------
    data_sampler : func or string
      - A function that takes two 2D numpy arrays of (lon, lat) as input,
        and returns an image of the original dataset sampled
        at these locations; see :mod:`toasty.samplers`.
      - A string giving a base toast directory that contains the
        base level of toasted tiles, using this option, only the
        merge step takes place, the given directory must contain
        a "depth" directory for the given depth parameter

    depth : int
      The maximum depth to tile to. A depth of N creates
      4^N pngs at the deepest level
    merge : bool or callable (default True)
      How to treat lower resolution tiles.

      - If True, tiles above the lowest level (highest resolution)
        will be computed by averaging and downsampling the 4 subtiles.
      - If False, sampler will be called explicitly for all tiles
      - If a callable object, this object will be passed the
        4x oversampled image to downsample

    base_level_only : bool (default False)
      If True only the bottem level of tiles will be created.
      In this case merge will be set to True, but no merging will happen,
      and only the highest resolution layer of images will be created.
    tile_filter: callable (optional)
      A function that takes a tile and determines if it is in toasting range.
      If not given default_tile_filter will be used which simply returns True.
    restart_dir: string (optional)
      For restart jobs, the directory in which to check for toast tiles
      before toasting (if tile is found, the toasting step is skipped)
    top: int (optional)
      The topmost layer of toast tiles to create (only relevant if
      base_level_only is False), default is 0.

    Yields
    ------
    (pth, tile) : str, ndarray
      pth is the relative path where the tile image should be saved
    """
    if tile_filter is None:
        tile_filter = lambda t: True

    if merge is True:
        merge = _default_merge

    parents = defaultdict(dict)

    for tile in iter_corners(max(depth, 1), bottom_only=merge, tile_filter=tile_filter):
        n, x, y = tile.pos.n, tile.pos.x, tile.pos.y

        if type(data_sampler) == str:
            img_dir = data_sampler + '/' + str(n) + '/'
            try:
                img = read_png(img_dir + str(y) + '/' + str(y) + '_' + str(x) + '.png')
            except: # could not read image
                img = None
        elif restart_dir and os.path.isfile(restart_dir + '/' + str(n) + '/' + str(y) + '/' + str(y) + '_' + str(x) + '.png'):
            img = None
        else:
            l, b = subsample(tile.corners[0], tile.corners[1], tile.corners[2], tile.corners[3], 256, tile.increasing)
            img = data_sampler(l, b)

        # No image was returned by the sampler,
        # either image data was not availible for the given ra/dec range
        # or it is a restart job, and that image was already computed
        if (img is None) and  base_level_only:
                continue

        if not base_level_only:
            for pth, img in _trickle_up(img, tile.pos, parents, merge, depth, top):
                if img is None:
                    continue
                yield pth, img
        else:
            pth = os.path.join('%i' % n, '%i' % y, '%i_%i.png' % (y, x))
            yield pth, img


def _trickle_up(im, node, parents, merge, depth, top=0):
    """
    When a new toast tile is ready, propagate it up the hierarchy
    and recursively yield its completed parents
    """

    n, x, y = node.n, node.x, node.y

    pth = os.path.join('%i' % n, '%i' % y, '%i_%i.png' % (y, x))

    nparent = sum(len(v) for v in parents.values())
    assert nparent <= 4 * max(depth, 1)

    if depth >= n: # handle special case of depth=0, n=1
        yield pth, im

    if n == top: # This is the uppermost level desired
        return

    # - If not merging and not at level 1, no need to accumulate
    if not merge and n > 1:
        return

    parent, xc, yc = _parent(node)
    corners = parents[parent]
    corners[(xc, yc)] = im

    if len(corners) < 4:  # parent not yet ready
        return

    parents.pop(parent)

    # imgs = [ul,ur,bl,br]
    #imgs = np.array([corners[(0, 0)],corners[(1, 0)],corners[(1, 0)],corners[(1, 1)]])

    ul = corners[(0, 0)]
    ur = corners[(1, 0)]
    bl = corners[(0, 1)]
    br = corners[(1, 1)]

    # dealing with any children lacking image data
    if all(x is None for x in [ul,ur,bl,br]):
        im = None
    else:
        # get img shape
        imgShape = [x for x in [ul,ur,bl,br] if x is not None][0].shape

        if not imgShape: # This shouldn't happen but...
            print([type(x) for x in [ul,ur,bl,br]])
            im = None
        else:

            if ul is None:
                ul = np.zeros(imgShape,dtype=np.uint8)
            if ur is None:
                ur = np.zeros(imgShape,dtype=np.uint8)
            if bl is None:
                bl = np.zeros(imgShape,dtype=np.uint8)
            if br is None:
                br = np.zeros(imgShape,dtype=np.uint8)

            try:
                mosaic = np.vstack((np.hstack((ul, ur)), np.hstack((bl, br))))
                im = (merge or _default_merge)(mosaic)
            except:
                print(imgShape)
                im = None


    for item in _trickle_up(im, parent, parents, merge, depth, top):
        yield item


def _default_merge(mosaic):
    """The default merge strategy -- just average all 4 pixels"""
    return (mosaic[::2, ::2] / 4. +
            mosaic[1::2, ::2] / 4. +
            mosaic[::2, 1::2] / 4. +
            mosaic[1::2, 1::2] / 4.).astype(mosaic.dtype)


def gen_wtml(base_dir, depth, **kwargs):
    """
    Create a minimal WTML record for a pyramid generated by toasty

    Parameters
    ----------
    base_dir : str
      The base path to a toast pyramid, as you wish for it to appear
      in the WTML file (i.e., this should be a path visible to a server)
    depth : int
      The maximum depth of the pyramid
    **kwargs
      Keyword arguments may be used to set parameters that appear in the
      generated WTML file. Keywords that are honored are:

      - FolderName
      - BandPass
      - Name
      - Credits
      - CreditsUrl
      - ThumbnailUrl

      Unhandled keywords are silently ignored.

    Returns
    -------
    wtml : str
      A WTML record
    """
    kwargs.setdefault('FolderName', 'Toasty')
    kwargs.setdefault('BandPass', 'Visible')
    kwargs.setdefault('Name', 'Toasty map')
    kwargs.setdefault('Credits', 'Toasty')
    kwargs.setdefault('CreditsUrl', 'http://github.com/ChrisBeaumont/toasty')
    kwargs.setdefault('ThumbnailUrl', '')
    kwargs['url'] = base_dir
    kwargs['depth'] = depth

    template = ('<Folder Name="{FolderName}">\n'
                '<ImageSet Generic="False" DataSetType="Sky" '
                'BandPass="{BandPass}" Name="{Name}" '
                'Url="{url}/{{1}}/{{3}}/{{3}}_{{2}}.png" BaseTileLevel="0" '
                'TileLevels="{depth}" BaseDegreesPerTile="180" '
                'FileType=".png" BottomsUp="False" Projection="Toast" '
                'QuadTreeMap="" CenterX="0" CenterY="0" OffsetX="0" '
                'OffsetY="0" Rotation="0" Sparse="False" '
                'ElevationModel="False">\n'
                '<Credits> {Credits} </Credits>\n'
                '<CreditsUrl>{CreditsUrl}</CreditsUrl>\n'
                '<ThumbnailUrl>{ThumbnailUrl}</ThumbnailUrl>\n'
                '<Description/>\n</ImageSet>\n</Folder>')
    return template.format(**kwargs)


def toast(data_sampler, depth, base_dir,
          wtml_file=None, merge=True, base_level_only=False,
          tile_filter=None, restart=False, top_layer=0):
    """Build a directory of toast tiles

    Parameters
    ----------
    data_sampler : func or string
      - A function of (lon, lat) that samples a dataset
        at the input 2D coordinate arrays
      - A string giving a base toast directory that contains the
        base level of toasted tiles, using this option, only the
        merge step takes place, the given directory must contain
        a "depth" directory for the given depth parameter
    depth : int
      The maximum depth to generate tiles for.
      4^n tiles are generated at each depth n
    base_dir : str
      The path to create the files at
    wtml_file : str (optional)
      The path to write a WTML file to. If not present,
      no file will be written
    merge : bool or callable (default True)
      How to treat lower resolution tiles.

      - If True, tiles above the lowest level (highest resolution)
        will be computed by averaging and downsampling the 4 subtiles.
      - If False, sampler will be called explicitly for all tiles
      - If a callable object, this object will be passed the
        4x oversampled image to downsample
    base_level_only : bool (default False)
      If True only the bottem level of tiles will be created.
      In this case merge will be set to True, but no merging will happen,
      and only the highest resolution layer of images will be created.
    tile_filter : callable or None (the default)
      An optional function ``accept_tile(tile) -> bool`` that filters tiles;
      only tiles for which the fuction returns ``True`` will be
      processed.
    top_layer: int (optional)
      If merging this indicates the uppermost layer to be created.

    """
    if wtml_file is not None:
        wtml = gen_wtml(base_dir, depth)
        with open(wtml_file, 'w') as outfile:
            outfile.write(wtml)

    if base_level_only:
        merge = True

    if restart:
        restart_dir = base_dir
    else:
        restart_dir = None

    num = 0
    for pth, tile in iter_tiles(data_sampler, depth, merge, base_level_only, tile_filter, restart_dir, top_layer):
        num += 1
        if num % 10 == 0:
            logging.getLogger(__name__).info("Finished %i of %i tiles" %
                                             (num, depth2tiles(depth)))
        pth = os.path.join(base_dir, pth)
        direc, _ = os.path.split(pth)
        if not os.path.exists(direc):
            try:
                os.makedirs(direc)
            except FileExistsError:
                print("%s already exists." % direc)
        try:
            save_png(pth, tile)
        except:
            print(pth)
            print(type(tile))
