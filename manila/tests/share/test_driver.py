# Copyright 2012 NetApp
# Copyright 2014 Mirantis Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
"""Unit tests for the Share driver module."""

import os
import time

import mock

from manila.common import constants
from manila import exception
from manila import network
from manila.share import configuration
from manila.share import driver
from manila import test
from manila import utils


def fake_execute_with_raise(*cmd, **kwargs):
    raise exception.ProcessExecutionError


def fake_sleep(duration):
    pass


class ShareDriverWithExecuteMixin(driver.ShareDriver, driver.ExecuteMixin):
    pass


class ShareDriverTestCase(test.TestCase):

    def setUp(self):
        super(ShareDriverTestCase, self).setUp()
        self.utils = utils
        self.stubs.Set(self.utils, 'execute', fake_execute_with_raise)
        self.time = time
        self.stubs.Set(self.time, 'sleep', fake_sleep)

    def test__try_execute(self):
        execute_mixin = ShareDriverWithExecuteMixin(
            configuration=configuration.Configuration(None))
        self.assertRaises(exception.ProcessExecutionError,
                          execute_mixin._try_execute)

    def test_verify_share_driver_mode_option_type(self):
        with utils.tempdir() as tmpdir:
            tmpfilename = os.path.join(tmpdir, 'share_driver_mode.conf')
            with open(tmpfilename, "w") as configfile:
                configfile.write("""[DEFAULT]\nshare_driver_mode = fake""")

            # Add config file with updated opt
            driver.CONF.default_config_files = [configfile.name]

            # Reload config instance to use redefined opt
            driver.CONF.reload_config_files()

            share_driver = driver.ShareDriver()
            self.assertEqual('fake', share_driver.mode)

    def _instantiate_share_driver(self, network_config_group):
        self.stubs.Set(network, 'API', mock.Mock())
        config = mock.Mock()
        config.append_config_values = mock.Mock()
        config.config_group = 'fake_config_group'
        config.network_config_group = network_config_group

        share_driver = driver.ShareDriver(configuration=config)

        self.assertTrue(hasattr(share_driver, 'configuration'))
        config.append_config_values.assert_called_once_with(driver.share_opts)
        if network_config_group:
            network.API.assert_called_once_with(
                config_group_name=config.network_config_group)
        else:
            network.API.assert_called_once_with(
                config_group_name=config.config_group)
        self.assertTrue(hasattr(share_driver, 'mode'))
        return share_driver

    def test_instantiate_share_driver(self):
        self._instantiate_share_driver(None)

    def test_instantiate_share_driver_another_config_group(self):
        self._instantiate_share_driver("fake_network_config_group")

    def test_instantiate_share_driver_no_configuration(self):
        self.stubs.Set(network, 'API', mock.Mock())

        share_driver = driver.ShareDriver(configuration=None)

        self.assertEqual(None, share_driver.configuration)
        network.API.assert_called_once_with(config_group_name=None)

    def test_get_driver_mode_empty_list(self):
        share_driver = self._instantiate_share_driver(None)
        self.assertRaises(
            exception.InvalidParameterValue,
            share_driver.get_driver_mode, [])

    def test_get_driver_mode_one_value_in_list_mode_is_not_set(self):
        share_driver = self._instantiate_share_driver(None)
        share_driver.mode = None

        mode = share_driver.get_driver_mode([constants.SINGLE_SVM_MODE, ])

        self.assertEqual(constants.SINGLE_SVM_MODE, mode)

    def test_get_driver_mode_one_value_in_list_mode_is_set_and_equal(self):
        share_driver = self._instantiate_share_driver(None)
        share_driver.mode = constants.SINGLE_SVM_MODE

        mode = share_driver.get_driver_mode([constants.SINGLE_SVM_MODE, ])

        self.assertEqual(constants.SINGLE_SVM_MODE, mode)

    def test_get_driver_mode_one_value_in_list_mode_is_set_and_not_equal(self):
        share_driver = self._instantiate_share_driver(None)
        share_driver.mode = constants.SINGLE_SVM_MODE

        self.assertRaises(
            exception.InvalidParameterValue,
            share_driver.get_driver_mode,
            [constants.MULTI_SVM_MODE, ])

    def test_get_driver_mode_two_values_in_list_mode_is_not_set(self):
        share_driver = self._instantiate_share_driver(None)
        share_driver.mode = None

        self.assertRaises(
            exception.InvalidParameterValue,
            share_driver.get_driver_mode,
            [constants.SINGLE_SVM_MODE, constants.MULTI_SVM_MODE])

    def test_get_driver_mode_two_values_in_list_mode_is_set(self):
        share_driver = self._instantiate_share_driver(None)
        share_driver.mode = constants.MULTI_SVM_MODE

        mode = share_driver.get_driver_mode(
            [constants.SINGLE_SVM_MODE, constants.MULTI_SVM_MODE, ])

        self.assertEqual(constants.MULTI_SVM_MODE, mode)

    def test_get_driver_mode_one_invalid_value_in_list_mode_is_not_set(self):
        share_driver = self._instantiate_share_driver(None)
        share_driver.mode = None

        self.assertRaises(
            exception.InvalidParameterValue,
            share_driver.get_driver_mode,
            ['fake', ])

    def test_get_driver_mode_one_valid_value_in_list_mode_is_invalid(self):
        share_driver = self._instantiate_share_driver(None)
        share_driver.mode = 'fake'

        self.assertRaises(
            exception.InvalidParameterValue,
            share_driver.get_driver_mode,
            [constants.MULTI_SVM_MODE, ])

    def test_get_driver_mode_two_values_in_list_invalid_mode_set(self):
        share_driver = self._instantiate_share_driver(None)
        share_driver.mode = 'fake'

        self.assertRaises(
            exception.InvalidParameterValue,
            share_driver.get_driver_mode,
            [constants.SINGLE_SVM_MODE, constants.MULTI_SVM_MODE, ])
