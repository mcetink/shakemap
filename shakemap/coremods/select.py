"""
Parse STREC output and create/select a GMPE set for an event.
"""
# stdlib imports
import os.path
import shutil
from collections import OrderedDict

# third party imports
from configobj import ConfigObj

# local imports
from .base import CoreModule
import shakemap.utils.config as cfg
from shakemap.utils.probs import get_weights
from shakemap.utils.layers import (validate_config,
                                   update_config_regions)
from shakelib.rupture.origin import Origin


class SelectModule(CoreModule):
    """
    select - Parse STREC output, make a GMPE set, create model_zc.conf.
    """

    command_name = 'select'
    targets = [r'model_zc\.conf']
    dependencies = [('event.xml', True), ('source.text', False)]
    configs = ['select.conf']

    def execute(self):
        '''
        Parses the output of STREC in accordance with the
        configuration file, creates a new GMPE set specific to the event,
        and writes model_zc.conf in the event's 'current' directory.

        Configuration file: select.conf

        Raises:
            NotADirectoryError -- the event's current directory doesn't exist
            FileNotFoundError -- the event.xml file doesn't exist
            ValidateError -- problems with the configuration file
            RuntimeError -- various problems matching the event to a gmpe set
        '''

        # ---------------------------------------------------------------------
        # Get the install and data paths and verify that the even directory
        # exists
        # ---------------------------------------------------------------------
        install_path, data_path = cfg.get_config_paths()
        datadir = os.path.join(data_path, self._eventid, 'current')
        if not os.path.isdir(datadir):
            raise NotADirectoryError('%s is not a valid directory' % datadir)
        # ---------------------------------------------------------------------
        # Open event.xml and make an Origin object
        # ---------------------------------------------------------------------
        eventxml = os.path.join(datadir, 'event.xml')
        if not os.path.isfile(eventxml):
            raise FileNotFoundError('%s does not exist.' % eventxml)
        momentfile = os.path.join(datadir, 'moment.xml')
        if not os.path.isfile(momentfile):
            momentfile = None
        sourcefile = os.path.join(datadir, 'source.txt')
        if not os.path.isfile(sourcefile):
            sourcefile = None

        org = Origin.fromFile(
            eventxml, sourcefile=sourcefile, momentfile=momentfile)

        #
        # Clear away results from previous runs
        #
        products_path = os.path.join(datadir, 'products')
        if os.path.isdir(products_path):
            shutil.rmtree(products_path, ignore_errors=True)

        # ---------------------------------------------------------------------
        # Get config file from install_path/config, parse and
        # validate it
        # ---------------------------------------------------------------------
        config = ConfigObj(os.path.join(install_path, 'config', 'select.conf'))
        validate_config(config, install_path)

        # ---------------------------------------------------------------------
        # Search through all custom regions, and the first one that we are
        # inside of, take its tectonic region config stuff and replace the
        # default tectonic regions.
        # ---------------------------------------------------------------------
        config = update_config_regions(org.lat, org.lon, config)

        # ---------------------------------------------------------------------
        # Get the default weighting for this event
        # ---------------------------------------------------------------------
        gmpe_list, weight_list, strec_results = get_weights(org, config)

        # ---------------------------------------------------------------------
        # Create ConfigObj object for output to model_zc.conf
        # ---------------------------------------------------------------------
        zc_file = os.path.join(datadir, 'model_zc.conf')
        zc_conf = ConfigObj(indent_type='    ')
        zc_conf.filename = zc_file
        #
        # Add the new gmpe set to the object
        #
        gmpe_set = 'gmpe_' + str(self._eventid) + '_custom'
        zc_conf['gmpe_sets'] = OrderedDict([
            (gmpe_set, OrderedDict([
                ('gmpes', list(gmpe_list)),
                ('weights', list(weight_list)),
                ('weights_large_dist', 'None'),
                ('dist_cutoff', 'nan'),
                ('site_gmpes', 'None'),
                ('weights_site_gmpes', 'None')
            ]))
        ])
        #
        # Set gmpe to use the new gmpe set
        #
        zc_conf['modeling'] = OrderedDict([
            ('gmpe', gmpe_set),
            ('mechanism', strec_results['FocalMechanism'])
        ])

        zc_conf.write()
