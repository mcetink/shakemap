# stdlib
import os.path
import time
import json
from datetime import datetime
from collections import OrderedDict

# third party
import fiona

from configobj import ConfigObj

from matplotlib.colors import LightSource
import matplotlib.pyplot as plt
from mpl_toolkits.basemap import Basemap
import matplotlib.patheffects as path_effects
from mpl_toolkits.axes_grid1 import make_axes_locatable

from shapely.geometry import MultiPolygon
from shapely.geometry import MultiLineString
from shapely.geometry import LineString
from shapely.geometry import GeometryCollection
from shapely.geometry import Polygon as sPolygon
from shapely.geometry import shape as sShape

import numpy as np
from descartes import PolygonPatch
from scipy.ndimage import gaussian_filter
import pandas as pd

# local imports
from mapio.gmt import GMTGrid
from impactutils.colors.cpalette import ColorPalette
from mapio.basemapcity import BasemapCities
from shakelib.utils.containers import ShakeMapOutputContainer
from shakelib.rupture.origin import Origin
from shakelib.rupture.point_rupture import PointRupture
from shakelib.rupture.factory import rupture_from_dict_and_origin
from shakelib.utils.imt_string import oq_to_file
from shakemap.coremods.contour import getContourLevels
from shakemap.utils.config import get_config_paths
from .base import CoreModule
from shakemap.utils.utils import path_macro_sub


CITY_COLS = 2
CITY_ROWS = 2
CITIES_PER_GRID = 10
FIG_WIDTH = 8
FIG_HEIGHT = 8
BASEMAP_RESOLUTION = 'c'
WATERCOLOR = '#7AA1DA'
NCONTOURS = 6
VERT_EXAG = 0.1

# all of the zorder values for different plotted parameters
# elements with a higher zorder will plot on top of lower elements.
IMG_ZORDER = 1
ROAD_ZORDER = 5
CONTOUR_ZORDER = 800
DASHED_CONTOUR_ZORDER = 1002
OCEAN_ZORDER = 1000
BORDER_ZORDER = 1001
FAULT_ZORDER = 1100
EPICENTER_ZORDER = 1100
STATIONS_ZORDER = 1150
CITIES_ZORDER = 1200
GRATICULE_ZORDER = 1200
SCALE_ZORDER = 1500

THUMBNAIL_WIDTH_PIXELS = 100

MMI_LABELS = {
    '1': 'I',
    '2': 'II',
    '3': 'III',
    '4': 'IV',
    '5': 'V',
    '6': 'VI',
    '7': 'VII',
    '8': 'VIII',
    '9': 'IX',
    '10': 'X'
}


class MappingModule(CoreModule):
    """
    mapping -- Generate maps of the IMTs found in shake_result.hdf.
    """

    command_name = 'mapping'

    # supply here a data structure with information about files that
    # can be created by this module.
    mapping_page = {'title':'Ground Motion Maps','slug':'maps'}
    contents = OrderedDict.fromkeys(['intensityMap','pgaMap','pgvMap','psa[PERIOD]Map'])
    contents['intensityMap'] = {'title':'Intensity Map',
                                'caption':'Map of macroseismic intensity.',
                                'page':mapping_page,
                                'formats':[{'filename':'intensity.jpg',
                                            'type':'image/jpeg'},
                                           {'filename':'intensity.pdf',
                                            'type':'application/pdf'}
                                          ]
                                }
    
    contents['pgaMap'] = {'title':'PGA Map',
                              'caption':'Map of peak ground acceleration (%g).',
                              'page':mapping_page,
                              'formats':[{'filename':'pga.jpg',
                                          'type':'image/jpeg'},
                                          {'filename':'PGA_contour.pdf',
                                          'type':'image/jpeg'}
                                          ]
                             }
    contents['pgvMap'] = {'title':'PGV Map',
                              'caption':'Map of peak ground velocity (cm/s).',
                              'page':mapping_page,
                              'formats':[{'filename':'pgv.jpg',
                                          'type':'image/jpeg'},
                                          {'filename':'PGV_contour.pdf',
                                          'type':'application/pdf'},
                                          ]
                             }
    psacap = 'Map of [FPERIOD] sec 5% damped pseudo-spectral acceleration(%g).'
    contents['psa[PERIOD]Map'] = {'title':'PSA[PERIOD] Map',
                                      'page':mapping_page,
                                      'caption':psacap,
                                      'formats':[{'filename':'psa[0-9][0-9].jpg',
                                                  'type':'image/jpeg'},
                                                 {'filename':'PSA[0-9]p[0-9]_contour.pdf',
                                                  'type':'application/pdf'},
                                                ]
                                     }
    
    def execute(self):
        """
        Raises:
            NotADirectoryError: When the event data directory does not exist.
            FileNotFoundError: When the the shake_result HDF file does not
                exist.
        """
        install_path, data_path = get_config_paths()
        datadir = os.path.join(data_path, self._eventid, 'current', 'products')
        if not os.path.isdir(datadir):
            raise NotADirectoryError('%s is not a valid directory.' % datadir)
        datafile = os.path.join(datadir, 'shake_result.hdf')
        if not os.path.isfile(datafile):
            raise FileNotFoundError('%s does not exist.' % datafile)

        # Open the ShakeMapOutputContainer and extract the data
        container = ShakeMapOutputContainer.load(datafile)
        if container.getDataType() != 'grid':
            raise NotImplementedError('mapping module can only operate on '
                                      'gridded data, not sets of points')

        # get the path to the products.conf file, load the config
        config_file = os.path.join(install_path, 'config', 'products.conf')
        config = ConfigObj(config_file)

        # create contour files
        self.logger.info('Mapping...')

        # get all of the pieces needed for the mapmaker
        layerdict = {}
        layers = config['products']['mapping']['layers']
        layerdict['coast'] = path_macro_sub(
            layers['coasts'], install_path, data_path)
        layerdict['ocean'] = path_macro_sub(
            layers['oceans'], install_path, data_path)
        layerdict['lake'] = path_macro_sub(
            layers['lakes'], install_path, data_path)
        layerdict['country'] = path_macro_sub(
            layers['countries'], install_path, data_path)
        layerdict['state'] = path_macro_sub(
            layers['states'], install_path, data_path)
        topofile = path_macro_sub(
            layers['topography'], install_path, data_path)
        cities = path_macro_sub(layers['cities'], install_path, data_path)
        mapmaker = MapMaker(container, topofile, layerdict, cities,
                            self.logger)
        self.logger.info('Drawing intensity map...')
        intensity_map = mapmaker.drawIntensityMap(datadir)
        self.logger.info('Created intensity map %s' % intensity_map)
        for imt in config['products']['mapping']['imts']:
            self.logger.info('Drawing %s contour map...' % imt)
            contour_file = mapmaker.drawContourMap(imt, datadir)
            self.logger.info('Created contour map %s' % contour_file)

        ###########################
        # Make a placeholder plot of standard deviation
        # fill this in later if necessary
        plt.figure(figsize=(FIG_WIDTH,FIG_HEIGHT))
        components = container.getComponents('PGA')
        if not len(components):
            return
        component = components[0]
        imt_dict = container.getIMTGrids('PGA',component)
        im = plt.imshow(imt_dict['std'].getData(),interpolation='none',
                        cmap='jet',vmin=0,vmax=1.25)
        plt.title('PGA Standard Deviation')
        ax = plt.gca() #get the current axes object

        #do some magic stuff to make a new axes
        divider = make_axes_locatable(ax)
        cax = divider.append_axes("right", size="5%", pad=0.05)
        #draw colorbar in new divider axes
        plt.colorbar(im, cax=cax)
        sd_file = os.path.join(datadir,'sd.jpg')
        plt.savefig(sd_file)
        ###########################
            

def getProjectedPolygon(polygon, m):
    """
    Project a lat/lon polygon into the projection defined by Basemap instance.

    Args:
        polygon (Polygon): Shapely polygon.
        m (Basemap): Basemap instance.

    Returns:
        Polygon: Shapely polygon, with vertices projected.
    """
    extlon, extlat = zip(*polygon.exterior.coords[:])
    extx, exty = m(extlon, extlat)
    extpts = list(zip(extx, exty))
    ints = []
    for interior in polygon.interiors:
        try:
            intlon, intlat = zip(*interior.coords[:])
        except:
            pass
        intx, inty = m(intlon, intlat)
        ints.append(list(zip(intx, inty)))
    ppolygon = sPolygon(extpts, ints)
    return ppolygon


def getProjectedPatches(polygon, m, edgecolor=WATERCOLOR):
    """
    Project polygon into Descartes PolygonPatch objects.

    Args:
        polygon (Polygon): Shapely Polygon or MultiPolygon object.
        m (Basemap): Basemap instance.
        edgecolor (str): Hex RGB string (i.e. '#7AA1DA')

    Returns:
        list: List of Descartes PolygonPatch objects.
    """

    patches = []
    if isinstance(polygon, MultiPolygon):
        for p in polygon:
            ppolygon = getProjectedPolygon(p, m)
            patch = PolygonPatch(
                ppolygon, facecolor=WATERCOLOR,
                edgecolor=edgecolor, zorder=OCEAN_ZORDER,
                linewidth=1, fill=True, visible=True)
            patches.append(patch)
    else:
        ppolygon = getProjectedPolygon(polygon, m)
        patch = PolygonPatch(
            ppolygon, facecolor=WATERCOLOR,
            edgecolor=edgecolor, zorder=OCEAN_ZORDER,
            linewidth=1, fill=True, visible=True)
        patches.append(patch)

    return patches


class MapMaker(object):
    """Create intensity raster map and PGV, PGA, and spectral contour maps.

    """

    def __init__(self, container, topofile, layerdict, cities_file, logger):
        """Initialize MapMaker object.

        Args:
            container (ShakeMapOutputContainer): ShakeMapOutputContainer object
                containing model results.
            topofile (str): Path to file containing global topography grid.
            layerdict (dict): Dictionary containing fields:

                - coast: Global coastline shapefile.
                - ocean: Global ocean shapefile.
                - lake: Global lakes shapefile.
                - country: Global country boundaries shapefile.
                - state: Global state (or equivalent) boundaries shapefile.
                - roads: Global roads directory containing directories with
                  regional shapefiles.

            cities_file (str): Path to geonames cities1000.txt file.
            logger (Logger): Python logging instance.

        Raises:
            KeyError: When any of layerdict keys are missing.
        """
        req_keys = set(['coast', 'ocean', 'lake', 'country', 'state'])
        if len(set(layerdict.keys()).intersection(req_keys)) != len(req_keys):
            raise KeyError(
                'layerdict input must have all keys from %s' % str(req_keys))
        self.container = container
        self.topofile = topofile
        self.layerdict = layerdict
        cities = BasemapCities.loadFromGeoNames(cities_file)
        self.cities = cities
        self.city_cols = CITY_COLS
        self.city_rows = CITY_ROWS
        self.cities_per_grid = CITIES_PER_GRID
        self.intensity_colormap = ColorPalette.fromPreset('mmi')
        self.contour_colormap = ColorPalette.fromPreset('shaketopo')
        station_dict = container.getStationDict()
        self.stations = station_dict
        rupture_dict = container.getRuptureDict()
        info_dict = json.loads(
            container.getString('info.json'))['input']['event_information']
        event_dict = {
            'eventsourcecode': info_dict['event_id'],
            'eventsource': info_dict['eventsource'],
            'lat': float(info_dict['latitude']),
            'lon': float(info_dict['longitude']),
            'depth': float(info_dict['depth']),
            'mag': float(info_dict['magnitude'])
        }
        origin = Origin(event_dict)
        if rupture_dict['features'][0]['geometry']['type'] == 'Point':
            rupture = PointRupture(origin)
        else:
            rupture = rupture_from_dict_and_origin(rupture_dict, origin)
        self.fault = rupture
        self.fig_width = FIG_WIDTH
        self.fig_height = FIG_HEIGHT
        self.logger = logger

        # clip all the vector data now so that map rendering will be fast
        t1 = time.time()
        self._clipBounds()
        t2 = time.time()
        self.logger.debug('%.1f seconds to clip vectors.' % (t2 - t1))

    def _selectRoads(self, roads_folder, bbox):
        """Select road shapes from roads directory.

        Args:
            roads_folder (str): Path to folder containing global roads data.
            bbox (tuple): Tuple of map bounds (xmin,ymin,xmax,ymax).

        Returns:
            list: list of Shapely geometries.
        """
        vshapes = []
        xmin, ymin, xmax, ymax = bbox
        bboxpoly = sPolygon([(xmin, ymax), (xmax, ymax),
                             (xmax, ymin), (xmin, ymin), (xmin, ymax)])
        for root, dirs, files in os.walk(roads_folder):
            for fname in files:
                if fname.endswith('.shp'):
                    filename = os.path.join(root, fname)
                    with fiona.open(filename, 'r') as f:
                        shapes = f.items(bbox=bbox)
                        for shapeidx, shape in shapes:
                            tshape = sShape(shape['geometry'])
                            intshape = tshape.intersection(bboxpoly)
                            vshapes.append(intshape)

        return vshapes

    def _clipBounds(self):
        """
        Clip input vector data to bounds of map.
        """
        # returns a list of GeoJSON-like mapping objects
        comp = self.container.getComponents('MMI')[0]
        imtdict = self.container.getIMTGrids('MMI', comp)
        geodict = imtdict['mean'].getGeoDict()
        xmin, xmax, ymin, ymax = (geodict.xmin, geodict.xmax,
                                  geodict.ymin, geodict.ymax)
        bbox = (xmin, ymin, xmax, ymax)
        bboxpoly = sPolygon([(xmin, ymax), (xmax, ymax),
                             (xmax, ymin), (xmin, ymin), (xmin, ymax)])
        self.vectors = {}
        for key, value in self.layerdict.items():
            vshapes = []
            f = fiona.open(value, 'r')
            shapes = f.items(bbox=bbox)
            for shapeidx, shape in shapes:
                tshape = sShape(shape['geometry'])
                try:
                    intshape = tshape.intersection(bboxpoly)
#                except TopologicalError as te:
                except Exception as te:
                    self.logger.warn('Failure to grab %s segment: "%s"'
                                     % (key, str(te)))
                    continue
                vshapes.append(intshape)
            self.logger.debug('Filename is %s' % value)
            f.close()
            self.vectors[key] = vshapes

    def setCityGrid(self, nx=2, ny=2, cities_per_grid=10):
        """Define grid fused to limit the number of cities plotted on the map.

        Args:
            nx (int): Number of columns in grid.
            ny (int): Number of rows in grid.
            cities_per_grid (int): Maximum number of cities to plot in each
                grid cell.
        """
        self.city_cols = nx
        self.city_rows = ny
        self.cities_per_grid = cities_per_grid

    def setFigureSize(self, figwidth, figheight):
        """Set the figure size in inches.

        Args:
            figwidth (float): Figure width in inches.
            figheight (float): Figure height in inches.
        """
        self.fig_width = figwidth
        self.fig_height = figheight

    def setCityList(self, dataframe):
        """Set the city list to an input dataframe.

        Args:
            dataframe (DataFrame): Pandas DataFrame whose columns include:

                - name: Name of the city (required).
                - lat: Latitude of city (required).
                - lon: Longitude of city (required).
                - pop: Population of city (optional).
                - iscap: Boolean indicating capital status (optional).
                - placement: String indicating where city label should be
                  placed relative to city coordinates, one of: E, W, N, S, NE,
                  SE, SW, NW.
                  (optional).
                - xoff: Longitude offset for label relative to city coordinates
                  (optional).
                - yoff: Latitude offset for label relative to city coordinates
                  (optional).

        """
        self.cities = BasemapCities(dataframe)  # may raise exception
        self.city_rows = None
        self.city_cols = None
        self.cities_per_grid = None

    def _setMap(self, gd):
        """Define the map extents, figure size, etc.

        Args:
            gd (GeoDict): MapIO GeoDict defining bounds/resolution of input
            ShakeMap.

        Returns:
            Basemap: Basemap instance, Mercator projection.
        """
        clon = gd.xmin + (gd.xmax - gd.xmin) / 2.0
        clat = gd.ymin + (gd.ymax - gd.ymin) / 2.0
        f = plt.figure(figsize=(self.fig_width, self.fig_height))
        ax = f.add_axes([0.1, 0.1, 0.8, 0.8])

        m = Basemap(llcrnrlon=gd.xmin, llcrnrlat=gd.ymin, urcrnrlon=gd.xmax,
                    urcrnrlat=gd.ymax, rsphere=(6378137.00, 6356752.3142),
                    resolution=BASEMAP_RESOLUTION, projection='merc',
                    lat_0=clat, lon_0=clon, lat_ts=clat, ax=ax,
                    suppress_ticks=True)
        return m

    def _projectGrid(self, data, m, gd):
        """Project 2D array to map projection.

        Args:
            data (ndarray): 2D Numpy array to be projected.
            m (Basemap): Basemap instance.
            gd (GeoDict): MapIO GeoDict object.

        Returns:
            ndarray: Input array projected to map projection.
        """
        # set up meshgrid to project topo and mmi data
        xmin = gd.xmin
        if gd.xmax < gd.xmin:
            xmin -= 360
        lons = np.linspace(xmin, gd.xmax, gd.nx)
        # backwards so it plots right side up
        lats = np.linspace(gd.ymax, gd.ymin, gd.ny)
        llons1, llats1 = np.meshgrid(lons, lats)
        pdata = m.transform_scalar(np.flipud(data), lons, lats[::-1],
                                   gd.nx, gd.ny, returnxy=False,
                                   checkbounds=False, order=1, masked=False)
        return pdata

    def _getDraped(self, data, topodata):
        """Get array of data "draped" on topography.

        Args:
            data (ndarray): 2D Numpy array.
            topodata (ndarray): 2D Numpy array.

        Returns:
            ndarray: Numpy array of data draped on topography.
        """

        maxvalue = self.intensity_colormap.vmax
        mmisc = data / maxvalue
        rgba_img = self.intensity_colormap.cmap(mmisc)
        rgb = np.squeeze(rgba_img[:, :, 0:3])
        # use lightsource class to make our shaded topography
        ls = LightSource(azdeg=135, altdeg=45)
        # intensity = ls.hillshade(ptopo,fraction=0.25,vert_exag=1.0)

        ls1 = LightSource(azdeg=120, altdeg=45)
        ls2 = LightSource(azdeg=225, altdeg=45)
        intensity1 = ls1.hillshade(
            topodata, fraction=0.25, vert_exag=VERT_EXAG)
        intensity2 = ls2.hillshade(
            topodata, fraction=0.25, vert_exag=VERT_EXAG)
        intensity = intensity1 * 0.5 + intensity2 * 0.5

        draped_hsv = ls.blend_hsv(rgb, np.expand_dims(intensity, 2))

        return draped_hsv

    def _drawBoundaries(self, m):
        """Draw all country/state boundaries on the map.

        Args:
            m (Basemap): Basemap instance.

        """
        allshapes = self.vectors['country'] + self.vectors['state']
        for shape in allshapes:
            # shape is a geojson-like mapping thing
            try:
                if hasattr(shape, 'exterior'):
                    blon, blat = zip(*shape.exterior.coords[:])
                else:
                    blon, blat = zip(*shape.coords[:])
                bx, by = m(blon, blat)
                m.plot(bx, by, 'k', zorder=BORDER_ZORDER)
            except NotImplementedError:
                for tshape in shape:
                    try:
                        blon, blat = zip(*tshape.exterior.coords[:])
                        bx, by = m(blon, blat)
                        m.plot(bx, by, 'k', zorder=BORDER_ZORDER)
                    except NotImplementedError:
                        continue

    def _drawRoads(self, m):
        """Draw all roads on the map.

        Args:
            m (Basemap): Basemap instance.

        """
        allshapes = self.vectors['roads']
        xmin = 9999999
        ymin = xmin
        xmax = -99999999
        ymax = xmax
        for shape in allshapes:
            # shape is a shapely geometry
            if isinstance(shape, (MultiLineString, GeometryCollection)):
                blon = []
                blat = []
                for mshape in shape:
                    tlon, tlat = zip(*mshape.coords[:])
                    blon += tlon
                    blat += tlat
            else:
                blon, blat = zip(*shape.coords[:])

            if not len(blon):
                continue
            if min(blon) < xmin:
                xmin = min(blon)
            if min(blat) < ymin:
                ymin = min(blat)
            if max(blon) > xmax:
                xmax = max(blon)
            if max(blat) > ymax:
                ymax = max(blat)
            bx, by = m(blon, blat)
            m.plot(bx, by, '#808080', zorder=ROAD_ZORDER)

    def _drawLakes(self, m, gd):
        """Draw all lakes on the map.

        Args:
            m (Basemap): Basemap instance.

        """
        lakes = self.vectors['lake']
        for lake in lakes:
            ppatches = getProjectedPatches(lake, m, edgecolor='k')
            for ppatch in ppatches:
                m.ax.add_patch(ppatch)

    def _drawOceans(self, m, gd):
        """Draw all oceans on the map.

        Args:
            m (Basemap): Basemap instance.

        """
        if len(self.vectors['ocean']):
            ocean = self.vectors['ocean'][0]  # this is one shapely polygon
            ppatches = getProjectedPatches(ocean, m)
            for ppatch in ppatches:
                m.ax.add_patch(ppatch)

    def _drawMapScale(self, m, gd):
        """Draw a map scale in the lower left corner of the map.

        Args:
            m (Basemap): Basemap instance.
            gd (GeoDict): MapIO GeoDict instance.
        """
        # where to set the center of the scale bar
        scalex = gd.xmin + (gd.xmax - gd.xmin) / 5.0
        scaley = gd.ymin + (gd.ymax - gd.ymin) / 10.0

        # how tall should scale bar be, in map units (km)?
        yoff = (0.01 * (m.ymax - m.ymin))

        # where should scale apply (center of map)
        clon = (gd.xmin + gd.xmax) / 2.0
        clat = (gd.ymin + gd.ymax) / 2.0

        # figure out a scalebar length that is approximately 30% of total
        # width in km
        map_width = (m.xmax - m.xmin) / 1000  # km
        lengths = np.array([25, 50, 75, 100, 125, 150, 175, 200, 250])
        lfracs = lengths / map_width
        length = lengths[np.abs(lfracs - 0.3).argmin()]

        m.drawmapscale(scalex, scaley, clon, clat, length,
                       barstyle='fancy', yoffset=yoff, zorder=SCALE_ZORDER)

    def _drawCoastlines(self, m, gd):
        """Draw all coastlines on the map.

        Args:
            m (Basemap): Basemap instance.
            gd (GeoDict): MapIO GeoDict instance.

        """
        coasts = self.vectors['coast']
        for coast in coasts:  # these are polygons?
            if isinstance(coast, sPolygon):
                clon, clat = zip(*coast.exterior.coords[:])
                cx, cy = m(clon, clat)
                m.plot(cx, cy, 'k', zorder=BORDER_ZORDER)
            elif isinstance(coast, LineString):
                clon, clat = zip(*coast.coords[:])
                cx, cy = m(clon, clat)
                m.plot(cx, cy, 'k', zorder=BORDER_ZORDER)
            else:
                for tshape in coast:
                    clon, clat = zip(*tshape.coords[:])
                    cx, cy = m(clon, clat)
                    m.plot(cx, cy, 'k', zorder=BORDER_ZORDER)

    def _drawGraticules(self, m, gd):
        """Draw meridian/parallels on the map.

        Args:
            m (Basemap): Basemap instance.
            gd (GeoDict): MapIO GeoDict instance.

        """
        par = np.arange(np.ceil(gd.ymin), np.floor(gd.ymax) + 1, 1.0)
        mer = np.arange(np.ceil(gd.xmin), np.floor(gd.xmax) + 1, 1.0)
        merdict = m.drawmeridians(mer, labels=[0, 0, 0, 1], fontsize=10,
                                  linewidth=0.5, color='gray',
                                  zorder=GRATICULE_ZORDER)
        pardict = m.drawparallels(par, labels=[1, 0, 0, 0], fontsize=10,
                                  linewidth=0.5, color='gray',
                                  zorder=GRATICULE_ZORDER)

        # loop over meridian and parallel dicts, change/increase font, draw
        # ticks
        xticks = []
        for merkey, mervalue in merdict.items():
            merline, merlablist = mervalue
            merlabel = merlablist[0]
            merlabel.set_family('sans-serif')
            merlabel.set_fontsize(12.0)
            xticks.append(merline[0].get_xdata()[0])

        yticks = []
        for parkey, parvalue in pardict.items():
            parline, parlablist = parvalue
            parlabel = parlablist[0]
            parlabel.set_family('sans-serif')
            parlabel.set_fontsize(12.0)
            yticks.append(parline[0].get_ydata()[0])

        # plt.tick_params(axis='both',color='k',direction='in')
        plt.xticks(xticks, ())
        plt.yticks(yticks, ())
        m.ax.tick_params(direction='out')

    def _drawTitle(self, imt):
        """Draw the map title.

        Args:
            imt (str): IMT that is being drawn on the map ('MMI', 'PGV',
                'PGA', 'SA(x.y)').
            isContour (bool): If true, use input imt, otherwise use MMI.

        """
        # Add a title
        origin = self.fault.getOrigin()
        hlon = origin.lon
        hlat = origin.lat
        edict = json.loads(self.container.getString(
            'info.json'))['input']['event_information']
        eloc = edict['event_description']
        etime = datetime.strptime(
            edict['origin_time'], '%Y-%m-%d %H:%M:%S')
        timestr = etime.strftime('%b %d, %Y %H:%M:%S')
        mag = origin.mag
        if hlon < 0:
            lonstr = 'W%.2f' % np.abs(hlon)
        else:
            lonstr = 'E%.2f' % hlon
        if hlat < 0:
            latstr = 'S%.2f' % np.abs(hlat)
        else:
            latstr = 'N%.2f' % hlat
        dep = origin.depth
        eid = edict['event_id']
#        tpl = (timestr, mag, latstr, lonstr, dep, eid)
        fmt = ('USGS ShakeMap (%s): %s\n %s UTC M%.1f %s %s '
               'Depth: %.1fkm ID:%s')
        tstr = fmt % (imt, eloc, timestr, mag, latstr, lonstr, dep, eid)
        # plt.suptitle('USGS ShakeMap (%s): %s' % (layername, eloc),
        #              fontsize=14, verticalalignment='bottom', y=0.95)
        # plt.title('%s UTC M%.1f %s %s Depth: %.1fkm ID:%s' %
        #           tpl, fontsize=10, verticalalignment='bottom')
        # plt.rc('text', usetex=True)
        # matplotlib.rcParams['text.latex.preamble']=[r"\usepackage{amsmath}"]
        plt.title(tstr, fontsize=10, verticalalignment='bottom')
        # plt.title(r"\TeX\ is Number "
        #   r"$\displaystyle\sum_{n=1}^\infty\frac{-e^{i\pi}}{2^n}$!",
        #   fontsize=16, color='gray')

    def _drawStations(self, m, fill=False, imt='PGA'):
        """Draw station locations on the map.

        Args:
            m (Basemap): Basemap instance.
            fill (bool): Whether or not to fill symbols.
            imt (str): One of ('MMI', 'PGA', 'PGV', or 'SA(x.x)')
        """
        dimt = imt.lower()

        # get the locations and values of the MMI observations
        mmi_dict = {
            'lat': [],
            'lon': [],
            'mmi': []
        }
        inst_dict = {
            'lat': [],
            'lon': [],
            'mmi': [],
            dimt: []
        }
        # Get the locations and values of the observed/instrumented
        # observations.

        for feature in self.stations['features']:
            lon, lat = feature['geometry']['coordinates']
            net = feature['properties']['network'].lower()

            # If the network matches one of these then it is an MMI
            # observation
            if net in ['dyfi', 'mmi', 'intensity', 'ciim']:
                # Append data from MMI features
                channel = feature['properties']['channels'][0]
                for amplitude in channel['amplitudes']:
                    if amplitude['name'] != 'mmi':
                        continue
                    mmi_dict['mmi'].append(float(amplitude['value']))
                    mmi_dict['lat'].append(lat)
                    mmi_dict['lon'].append(lon)
            else:
                # Otherwise, the feature is an instrument

                # If dimt is MMI then we have to use the converted value
                # from an instrumental value
                mmi_conv = float(feature['properties']['intensity'])
                if dimt == 'mmi':
                    inst_dict[dimt].append(mmi_conv)
                    inst_dict['lat'].append(lat)
                    inst_dict['lon'].append(lon)
                else:
                    # Other IMTs are given in the channel for instruments
                    channel = feature['properties']['channels'][0]
                    for amplitude in channel['amplitudes']:
                        if amplitude['name'] != dimt:
                            continue
                        if amplitude['value'] == 'null':
                            continue
                        inst_dict[dimt].append(float(amplitude['value']))
                        inst_dict['lat'].append(lat)
                        inst_dict['lon'].append(lon)
                        # Add mmi also for the fill color of symbols
                        inst_dict['mmi'].append(mmi_conv)

        if fill:
            # Fill the symbols with the color for the intensity
            for i in range(len(mmi_dict['lat'])):
                mlat = mmi_dict['lat'][i]
                mlon = mmi_dict['lon'][i]
                mmi = mmi_dict['mmi'][i]
                mcolor = self.intensity_colormap.getDataColor(mmi)
                m.plot(mlon, mlat, 'o', latlon=True,
                       markerfacecolor=mcolor, markeredgecolor='k',
                       markersize=4, zorder=STATIONS_ZORDER)

            for i in range(len(inst_dict['lat'])):
                mlat = inst_dict['lat'][i]
                mlon = inst_dict['lon'][i]
                #
                # TODO: Make the fill color correspond to the mmi
                # obtained from the IMT.
                #
                mmi = inst_dict['mmi'][i]
                mcolor = self.intensity_colormap.getDataColor(mmi)
                m.plot(mlon, mlat, '^', latlon=True,
                       markerfacecolor=mcolor, markeredgecolor='k',
                       markersize=6, zorder=STATIONS_ZORDER)
        else:
            # Do not fill symbols

            # plot MMI as small circles
            mmilat = mmi_dict['lat']
            mmilon = mmi_dict['lon']
            m.plot(mmilon, mmilat, 'ko', latlon=True, fillstyle='none',
                   markersize=4, zorder=STATIONS_ZORDER)

            # plot MMI as slightly larger triangles
            instlat = inst_dict['lat']
            instlon = inst_dict['lon']
            m.plot(instlon, instlat, 'k^', latlon=True, fillstyle='none',
                   markersize=6, zorder=STATIONS_ZORDER)

    def _drawFault(self, m):
        """Draw fault rupture on the map.

        Args:
            m (Basemap): Basemap instance.

        """
        lats = self.fault.lats
        lons = self.fault.lons
        x, y = m(lons, lats)
        m.plot(x, y, 'k', lw=2, zorder=FAULT_ZORDER)

    def drawIntensityMap(self, outfolder):
        """
        Render the MMI data as intensity draped over topography, with oceans,
        coastlines, etc.

        Args:
            outfolder (str): Path to directory where output map should be
                saved.

        Returns:
            str: Path to output intensity map.
        """
        t0 = time.time()
        # resample shakemap to topogrid
        # get the geodict for the topo file
        topodict = GMTGrid.getFileGeoDict(self.topofile)[0]
        # get the geodict for the ShakeMap
        comp = self.container.getComponents('MMI')[0]
        imtdict = self.container.getIMTGrids('MMI', comp)
        mmigrid = imtdict['mean']
        smdict = mmigrid.getGeoDict()
        # get a geodict that is aligned with topo, but inside shakemap
        sampledict = topodict.getBoundsWithin(smdict)

        mmigrid = mmigrid.interpolateToGrid(sampledict)

        gd = mmigrid.getGeoDict()

        # establish the basemap object
        m = self._setMap(gd)

        # get topo layer and project it
        topogrid = GMTGrid.load(
            self.topofile, samplegeodict=sampledict, resample=False)
        topodata = topogrid.getData().copy()
        ptopo = self._projectGrid(topodata, m, gd)

        # get intensity layer and project it
        imtdata = mmigrid.getData().copy()
        pimt = self._projectGrid(imtdata, m, gd)

        # get the draped intensity data
        draped_hsv = self._getDraped(pimt, ptopo)  # where will 10.0 come from

        # draw the draped intensity data
        m.imshow(draped_hsv, interpolation='none', zorder=IMG_ZORDER)

        # draw country/state boundaries
        self._drawBoundaries(m)

        # draw whatever road data is available
        # self.logger.debug('Drawing roads...')
        # self._drawRoads(m)
        # self.logger.debug('Done drawing roads...')

        # draw lakes
        self._drawLakes(m, gd)

        # draw oceans (pre-processed with islands taken out)
        t1 = time.time()
        self._drawOceans(m, gd)
        t2 = time.time()
        self.logger.debug('%.1f seconds to render oceans.' % (t2 - t1))

        # draw coastlines
        self._drawCoastlines(m, gd)

        # draw meridians, parallels, labels, ticks
        self._drawGraticules(m, gd)

        # draw map scale
        self._drawMapScale(m, gd)

        # draw fault polygon, if present
        self._drawFault(m)  # get the fault loaded

        # draw epicenter
        origin = self.fault.getOrigin()
        hlon = origin.lon
        hlat = origin.lat
        m.plot(hlon, hlat, 'k*', latlon=True, fillstyle='none',
               markersize=22, mew=1.2, zorder=EPICENTER_ZORDER)

        # draw cities
        # reduce the number of cities to those whose labels don't collide
        # set up cities
        if self.city_cols is not None:
            self.cities = self.cities.limitByBounds(
                (gd.xmin, gd.xmax, gd.ymin, gd.ymax))
            self.cities = self.cities.limitByGrid(
                nx=self.city_cols, ny=self.city_rows,
                cities_per_grid=self.cities_per_grid)
            # self.logger.debug("Available fonts: ", self.cities._fontlist)
            if 'Times New Roman' in self.cities._fontlist:
                font = 'Times New Roman'
            else:
                font = 'DejaVu Sans'
            self.cities = self.cities.limitByMapCollision(m, fontname=font)
        self.cities.renderToMap(m.ax, zorder=CITIES_ZORDER)

        # draw title and supertitle
        self._drawTitle('MMI')

        # draw station and macroseismic locations
        self._drawStations(m)  # need stationlist object

        # save plot to file
        plt.draw()
        outfile = os.path.join(outfolder, 'intensity.pdf')
        plt.savefig(outfile)
        outfile2 = os.path.join(outfolder, 'intensity.jpg')
        plt.savefig(outfile2)
        tn = time.time()
        self.logger.debug('%.1f seconds to render entire map.' % (tn - t0))

        # make the thumbnail image
        dpi = THUMBNAIL_WIDTH_PIXELS / self.fig_width 
        thumbfile = os.path.join(outfolder, 'pin-thumbnail.png')
        plt.savefig(thumbfile,dpi=dpi)
        
        return outfile

    def _getShaded(self, ptopo):
        """Get shaded topography.

        Args:
            ptopo (ndarray): Numpy array of projected topography data.

        Returns:
            ndarray: Numpy array of light-shaded topography.

        """
        maxvalue = self.contour_colormap.vmax
        ls1 = LightSource(azdeg=120, altdeg=45)
        ls2 = LightSource(azdeg=225, altdeg=45)
        intensity1 = ls1.hillshade(ptopo, fraction=0.25, vert_exag=VERT_EXAG)
        intensity2 = ls2.hillshade(ptopo, fraction=0.25, vert_exag=VERT_EXAG)
        intensity = intensity1 * 0.5 + intensity2 * 0.5

        ptoposc = ptopo / maxvalue
        rgba = self.contour_colormap.cmap(ptoposc)
        rgb = np.squeeze(rgba)

        draped_hsv = ls1.blend_hsv(rgb, np.expand_dims(intensity, 2))

        return draped_hsv

    def round_to(self, n, precision):
        """Round number to nearest level desired precision.

        Example: round_to(22.1, 10) => 20.

        Args:
            n (float): Input number to round.
            precision (int): Desired precision.

        Returns:
            int: Rounded value.

        """
        correction = 0.5 if n >= 0 else -0.5
        return int(n / precision + correction) * precision

    def drawContourMap(self, imt, outfolder, cmin=None, cmax=None):
        """
        Render IMT data as contours over topography, with oceans, coastlines,
        etc.

        Args:
            outfolder (str): Path to directory where output map should be
                saved.

        Returns:
            str: Path to output IMT map.
        """
        if self.contour_colormap is None:
            raise Exception('MapMaker.setGMTColormap() has not been called.')
        t0 = time.time()
        # resample shakemap to topogrid
        # get the geodict for the topo file
        topodict = GMTGrid.getFileGeoDict(self.topofile)[0]
        # get the geodict for the ShakeMap
        comp = self.container.getComponents(imt)[0]
        imtdict = self.container.getIMTGrids(imt, comp)
        imtgrid = imtdict['mean']
        smdict = imtgrid.getGeoDict()
        # get a geodict that is aligned with topo, but inside shakemap
        sampledict = topodict.getBoundsWithin(smdict)

        imtgrid = imtgrid.interpolateToGrid(sampledict)

        gd = imtgrid.getGeoDict()

        # establish the basemap object
        m = self._setMap(gd)

        # get topo layer and project it
        topogrid = GMTGrid.load(
            self.topofile, samplegeodict=sampledict, resample=False)
        topodata = topogrid.getData().copy()
        ptopo = self._projectGrid(topodata, m, gd)

        # get contour layer and project it1
        imtdata = imtgrid.getData().copy()

        # convert units if necessary
        if imt == 'MMI':
            pass
        elif imt == 'PGV':
            imtdata = np.exp(imtdata)
        else:
            imtdata = np.exp(imtdata) * 100

        pimt = self._projectGrid(imtdata, m, gd)

        # get the draped intensity data
        hillshade = self._getShaded(ptopo)

        # draw the draped intensity data
        m.imshow(hillshade, interpolation='none', zorder=IMG_ZORDER)

        # draw the contours of imt data
        xmin = gd.xmin
        if gd.xmax < gd.xmin:
            xmin -= 360
        lons = np.linspace(xmin, gd.xmax, gd.nx)
        # backwards so it plots right side up
        lats = np.linspace(gd.ymax, gd.ymin, gd.ny)
        x, y = m(*np.meshgrid(lons, lats))
        pimt = gaussian_filter(pimt, 5.0)
        dmin = pimt.min()
        dmax = pimt.max()
        if imt == 'MMI':
            levels = getContourLevels(dmin, dmax, itype='linear')
            mmimap = ColorPalette.fromPreset('mmi')

            for level in levels:
                # First, draw contours at half unit locations
                if (level * 2) % 2 == 1:
                    # Show dashed contours everywhere
                    m.contour(x, y, np.flipud(pimt),
                              linestyles='dashed',
                              cmap=mmimap.cmap,
                              vmin=mmimap.vmin,
                              vmax=mmimap.vmax,
                              levels=level,
                              zorder=DASHED_CONTOUR_ZORDER)

                    # Solid contours on land
                    m.contour(x, y, np.flipud(pimt),
                              cmap=mmimap.cmap,
                              vmin=mmimap.vmin,
                              vmax=mmimap.vmax,
                              levels=level,
                              zorder=CONTOUR_ZORDER)

                # Put contour labels at the whole unit locations
                else:
                    cs = m.contour(x, y, np.flipud(pimt),
                                   linestyles='dashed',
                                   cmap=mmimap.cmap,
                                   vmin=mmimap.vmin,
                                   vmax=mmimap.vmax,
                                   levels=level,
                                   linewidths=0.0,
                                   zorder=DASHED_CONTOUR_ZORDER)
                    labs = cs.clabel(fontsize=0, fmt='%.0f')
                    for clabel in labs:
                        cx, cy = clabel.get_position()
                        label_str = clabel.get_text()
                        roman_label = MMI_LABELS[label_str]
                        th = plt.text(cx, cy, roman_label,
                                      zorder=DASHED_CONTOUR_ZORDER,
                                      ha='center',
                                      va='center',
                                      color='black',
                                      weight='normal',
                                      size=16)
                        th.set_path_effects(
                            [path_effects.Stroke(linewidth=2.0,
                                                 foreground='white'),
                             path_effects.Normal()])

        else:
            levels = getContourLevels(dmin, dmax)

            # Show dashed contours everywhere
            m.contour(x, y, np.flipud(pimt), colors='w',
                      linestyles='dashed',
                      cmap=None,
                      levels=levels,
                      zorder=DASHED_CONTOUR_ZORDER)

            # Solid contours on land
            cs = m.contour(x, y, np.flipud(pimt), colors='w',
                           cmap=None,
                           levels=levels,
                           zorder=CONTOUR_ZORDER)
            clabels = plt.clabel(cs, colors='k', fmt='%.3g',
                                 fontsize=8.0,
                                 zorder=CONTOUR_ZORDER)

            # Put labels at the dashed zorder so that they are not maksed by
            # oceans
            for cl in clabels:
                bbox = dict(boxstyle="round", facecolor='white', edgecolor='w')
                cl.set_bbox(bbox)
                cl.set_zorder(DASHED_CONTOUR_ZORDER)

        # draw country/state boundaries
        self._drawBoundaries(m)

        # draw lakes
        self._drawLakes(m, gd)

        # draw oceans (pre-processed with islands taken out)
        t1 = time.time()
        self._drawOceans(m, gd)
        t2 = time.time()
        self.logger.debug('%.1f seconds to render oceans.' % (t2 - t1))

        # draw coastlines
        self._drawCoastlines(m, gd)

        # draw meridians, parallels, labels, ticks
        self._drawGraticules(m, gd)

        # draw filled symbols for MMI and instrumented measures
        self._drawStations(m, fill=True, imt=imt)

        # draw map scale
        self._drawMapScale(m, gd)

        # draw fault polygon, if present
        self._drawFault(m)  # get the fault loaded

        # draw epicenter
        origin = self.fault.getOrigin()
        hlon = origin.lon
        hlat = origin.lat
        m.plot(hlon, hlat, 'k*', latlon=True, fillstyle='none',
               markersize=22, mew=1.2, zorder=EPICENTER_ZORDER)

        # draw cities
        # reduce the number of cities to those whose labels don't collide
        # set up cities
        if self.city_cols is not None:
            self.cities = self.cities.limitByBounds(
                (gd.xmin, gd.xmax, gd.ymin, gd.ymax))
            self.cities = self.cities.limitByGrid(
                nx=self.city_cols, ny=self.city_rows,
                cities_per_grid=self.cities_per_grid)
            if 'Times New Roman' in self.cities._fontlist:
                font = 'Times New Roman'
            else:
                font = 'DejaVu Sans'
            self.cities = self.cities.limitByMapCollision(m, fontname=font)
        self.cities.renderToMap(m.ax, zorder=CITIES_ZORDER)

        # draw title and supertitle
        self._drawTitle(imt)

        # save plot to file
        fileimt = oq_to_file(imt)
        plt.draw()
        outfile = os.path.join(outfolder, '%s_contour.pdf' %
                               (fileimt))
        plt.savefig(outfile)

        ###################
        # to be compatible with the current NEIC web rendering,
        # we're going to make another copy of the file with the
        # old style naming convention.  Stop making this when
        # all contributing networks are using this code.
        if 'PSA' in fileimt:
            oldimt = fileimt.replace('p','').lower()
        else:
            oldimt = fileimt.lower()
        outfile_old = os.path.join(outfolder, '%s.jpg' % oldimt)
        plt.savefig(outfile_old)
        ######################

        
        
        tn = time.time()
        self.logger.debug('%.1f seconds to render entire map.' % (tn - t0))
        return outfile
