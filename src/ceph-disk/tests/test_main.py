from mock import patch, DEFAULT
import os
import pytest
import io
import subprocess
import unittest
from ceph_disk import main


def fail_to_mount(dev, fstype, options):
    raise main.MountError(dev + " mount fail")


class TestCephDisk(object):

    def setup_class(self):
        main.setup_logging(verbose=True, log_stdout=False)

    def test_main_list_json(self, capsys):
        args = main.parse_args(['list', '--format', 'json'])
        with patch.multiple(
                main,
                list_devices=lambda args: {}):
            main.main_list(args)
            out, err = capsys.readouterr()
            assert '{}\n' == out

    def test_main_list_plain(self, capsys):
        args = main.parse_args(['list'])
        with patch.multiple(
                main,
                list_devices=lambda args: {}):
            main.main_list(args)
            out, err = capsys.readouterr()
            assert '' == out

    def test_list_format_more_osd_info_plain(self):
        dev = {
            'ceph_fsid': 'UUID',
            'cluster': 'ceph',
            'whoami': '1234',
            'journal_dev': '/dev/Xda2',
        }
        out = main.list_format_more_osd_info_plain(dev)
        assert dev['cluster'] in " ".join(out)
        assert dev['journal_dev'] in " ".join(out)
        assert dev['whoami'] in " ".join(out)

        dev = {
            'ceph_fsid': 'UUID',
            'whoami': '1234',
            'journal_dev': '/dev/Xda2',
        }
        out = main.list_format_more_osd_info_plain(dev)
        assert 'unknown cluster' in " ".join(out)

    def test_list_format_plain(self):
        payload = [{
            'path': '/dev/Xda',
            'ptype': 'unknown',
            'type': 'other',
            'mount': '/somewhere',
        }]
        out = main.list_format_plain(payload)
        assert payload[0]['path'] in out
        assert payload[0]['type'] in out
        assert payload[0]['mount'] in out

        payload = [{
            'path': '/dev/Xda1',
            'ptype': 'unknown',
            'type': 'swap',
        }]
        out = main.list_format_plain(payload)
        assert payload[0]['path'] in out
        assert payload[0]['type'] in out

        payload = [{
            'path': '/dev/Xda',
            'partitions': [
                {
                    'dmcrypt': {},
                    'ptype': 'whatever',
                    'is_partition': True,
                    'fs_type': 'ext4',
                    'path': '/dev/Xda1',
                    'mounted': '/somewhere',
                    'type': 'other',
                }
            ],
        }]
        out = main.list_format_plain(payload)
        assert payload[0]['path'] in out
        assert payload[0]['partitions'][0]['path'] in out

    def test_list_format_dev_plain(dev):
        #
        # data
        #
        dev = {
            'path': '/dev/Xda1',
            'ptype': main.OSD_UUID,
            'state': 'prepared',
            'whoami': '1234',
        }
        out = main.list_format_dev_plain(dev)
        assert 'data' in out
        assert dev['whoami'] in out
        assert dev['state'] in out
        #
        # journal
        #
        dev = {
            'path': '/dev/Xda2',
            'ptype': main.JOURNAL_UUID,
            'journal_for': '/dev/Xda1',
        }
        out = main.list_format_dev_plain(dev)
        assert 'journal' in out
        assert dev['journal_for'] in out

        #
        # dmcrypt data
        #
        ptype2type = {
            main.DMCRYPT_OSD_UUID: 'plain',
            main.DMCRYPT_LUKS_OSD_UUID: 'LUKS',
        }
        for (ptype, type) in ptype2type.iteritems():
            for holders in ((), ("dm_0",), ("dm_0", "dm_1")):
                devices = [{
                    'path': '/dev/dm_0',
                    'whoami': '1234',
                }]
                dev = {
                    'dmcrypt': {
                        'holders': holders,
                        'type': type,
                    },
                    'path': '/dev/Xda1',
                    'ptype': ptype,
                    'state': 'prepared',
                }
                with patch.multiple(
                        main,
                        list_devices=lambda path: devices,
                ):
                    out = main.list_format_dev_plain(dev, devices)
                assert 'data' in out
                assert 'dmcrypt' in out
                assert type in out
                if len(holders) == 1:
                    assert devices[0]['whoami'] in out
                for holder in holders:
                    assert holder in out

        #
        # dmcrypt journal
        #
        ptype2type = {
            main.DMCRYPT_JOURNAL_UUID: 'plain',
            main.DMCRYPT_LUKS_JOURNAL_UUID: 'LUKS',
        }
        for (ptype, type) in ptype2type.iteritems():
            for holders in ((), ("dm_0",)):
                dev = {
                    'path': '/dev/Xda2',
                    'ptype': ptype,
                    'journal_for': '/dev/Xda1',
                    'dmcrypt': {
                        'holders': holders,
                        'type': type,
                    },
                }
                out = main.list_format_dev_plain(dev, devices)
                assert 'journal' in out
                assert 'dmcrypt' in out
                assert type in out
                assert dev['journal_for'] in out
                if len(holders) == 1:
                    assert holders[0] in out

    def test_list_dev_osd(self):
        dev = "Xda"
        mount_path = '/mount/path'
        fs_type = 'ext4'
        cluster = 'ceph'
        uuid_map = {}

        def more_osd_info(path, uuid_map, desc):
            desc['cluster'] = cluster
        #
        # mounted therefore active
        #
        with patch.multiple(
                main,
                is_mounted=lambda dev: mount_path,
                get_dev_fs=lambda dev: fs_type,
                more_osd_info=more_osd_info
        ):
            desc = {}
            main.list_dev_osd(dev, uuid_map, desc)
            assert {'cluster': 'ceph',
                    'fs_type': 'ext4',
                    'mount': '/mount/path',
                    'state': 'active'} == desc
        #
        # not mounted and cannot mount: unprepared
        #
        mount_path = None
        with patch.multiple(
                main,
                is_mounted=lambda dev: mount_path,
                get_dev_fs=lambda dev: fs_type,
                mount=fail_to_mount,
                more_osd_info=more_osd_info
        ):
            desc = {}
            main.list_dev_osd(dev, uuid_map, desc)
            assert {'fs_type': 'ext4',
                    'mount': mount_path,
                    'state': 'unprepared'} == desc
        #
        # not mounted and magic found: prepared
        #

        def get_oneliner(path, what):
            if what == 'magic':
                return main.CEPH_OSD_ONDISK_MAGIC
            else:
                raise Exception('unknown ' + what)
        with patch.multiple(
                main,
                is_mounted=lambda dev: mount_path,
                get_dev_fs=lambda dev: fs_type,
                mount=DEFAULT,
                unmount=DEFAULT,
                get_oneliner=get_oneliner,
                more_osd_info=more_osd_info
        ):
            desc = {}
            main.list_dev_osd(dev, uuid_map, desc)
            assert {'cluster': 'ceph',
                    'fs_type': 'ext4',
                    'mount': mount_path,
                    'magic': main.CEPH_OSD_ONDISK_MAGIC,
                    'state': 'prepared'} == desc

    @patch('os.path.exists')
    def test_list_paths_to_names(self, m_exists):

        def exists(path):
            return path in (
                '/sys/block/sda',
                '/sys/block/sdb',
                '/sys/block/cciss!c0d0',
                '/sys/block/cciss!c0d1',
                '/sys/block/cciss!c0d2',
            )

        m_exists.side_effect = exists
        paths = [
            '/dev/sda',
            '/dev/cciss/c0d0',
            'cciss!c0d1',
            'cciss/c0d2',
            'sdb',
        ]
        expected = [
            'sda',
            'cciss!c0d0',
            'cciss!c0d1',
            'cciss!c0d2',
            'sdb',
        ]
        assert expected == main.list_paths_to_names(paths)
        with pytest.raises(main.Error) as excinfo:
            main.list_paths_to_names(['unknown'])
        assert 'unknown' in excinfo.value.message

    def test_list_all_partitions(self):
        disk = "Xda"
        partition = "Xda1"

        with patch(
                'ceph_disk.main.os',
                listdir=lambda path: [disk],
        ), patch.multiple(
            main,
            list_partitions=lambda dev: [partition],
        ):
                assert {disk: [partition]} == main.list_all_partitions([])

        with patch.multiple(
                main,
                list_partitions=lambda dev: [partition],
        ):
                assert {disk: [partition]} == main.list_all_partitions([disk])

    def test_list_data(self):
        args = main.parse_args(['list'])
        #
        # a data partition that fails to mount is silently
        # ignored
        #
        partition_uuid = "56244cf5-83ef-4984-888a-2d8b8e0e04b2"
        disk = "Xda"
        partition = "Xda1"
        fs_type = "ext4"

        with patch.multiple(
                main,
                list_all_partitions=lambda names: {disk: [partition]},
                get_partition_uuid=lambda dev: partition_uuid,
                get_partition_type=lambda dev: main.OSD_UUID,
                get_dev_fs=lambda dev: fs_type,
                mount=fail_to_mount,
                unmount=DEFAULT,
                is_partition=lambda dev: True,
        ):
            expect = [{'path': '/dev/' + disk,
                       'partitions': [{
                           'dmcrypt': {},
                           'fs_type': fs_type,
                           'is_partition': True,
                           'mount': None,
                           'path': '/dev/' + partition,
                           'ptype': main.OSD_UUID,
                           'state': 'unprepared',
                           'type': 'data',
                           'uuid': partition_uuid,
                       }]}]
            assert expect == main.list_devices(args)

    def test_list_dmcrypt_data(self):
        args = main.parse_args(['list'])
        partition_type2type = {
            main.DMCRYPT_OSD_UUID: 'plain',
            main.DMCRYPT_LUKS_OSD_UUID: 'LUKS',
        }
        for (partition_type, type) in partition_type2type.iteritems():
            #
            # dmcrypt data partition with one holder
            #
            partition_uuid = "56244cf5-83ef-4984-888a-2d8b8e0e04b2"
            disk = "Xda"
            partition = "Xda1"
            holders = ["dm-0"]
            with patch.multiple(
                    main,
                    is_held=lambda dev: holders,
                    list_all_partitions=lambda names: {disk: [partition]},
                    get_partition_uuid=lambda dev: partition_uuid,
                    get_partition_type=lambda dev: partition_type,
                    is_partition=lambda dev: True,
            ):
                expect = [{'path': '/dev/' + disk,
                           'partitions': [{
                               'dmcrypt': {
                                   'holders': holders,
                                   'type': type,
                               },
                               'fs_type': None,
                               'is_partition': True,
                               'mount': None,
                               'path': '/dev/' + partition,
                               'ptype': partition_type,
                               'state': 'unprepared',
                               'type': 'data',
                               'uuid': partition_uuid,
                           }]}]
                assert expect == main.list_devices(args)
            #
            # dmcrypt data partition with two holders
            #
            partition_uuid = "56244cf5-83ef-4984-888a-2d8b8e0e04b2"
            disk = "Xda"
            partition = "Xda1"
            holders = ["dm-0", "dm-1"]
            with patch.multiple(
                    main,
                    is_held=lambda dev: holders,
                    list_all_partitions=lambda names: {disk: [partition]},
                    get_partition_uuid=lambda dev: partition_uuid,
                    get_partition_type=lambda dev: partition_type,
                    is_partition=lambda dev: True,
            ):
                expect = [{'path': '/dev/' + disk,
                           'partitions': [{
                               'dmcrypt': {
                                   'holders': holders,
                                   'type': type,
                               },
                               'is_partition': True,
                               'path': '/dev/' + partition,
                               'ptype': partition_type,
                               'type': 'data',
                               'uuid': partition_uuid,
                           }]}]
                assert expect == main.list_devices(args)

    def test_list_multipath(self):
        args = main.parse_args(['list'])
        #
        # multipath data partition
        #
        partition_uuid = "56244cf5-83ef-4984-888a-2d8b8e0e04b2"
        disk = "Xda"
        partition = "Xda1"
        with patch.multiple(
                main,
                list_all_partitions=lambda names: {disk: [partition]},
                get_partition_uuid=lambda dev: partition_uuid,
                get_partition_type=lambda dev: main.MPATH_OSD_UUID,
                is_partition=lambda dev: True,
        ):
            expect = [{'path': '/dev/' + disk,
                       'partitions': [{
                           'dmcrypt': {},
                           'fs_type': None,
                           'is_partition': True,
                           'mount': None,
                           'multipath': True,
                           'path': '/dev/' + partition,
                           'ptype': main.MPATH_OSD_UUID,
                           'state': 'unprepared',
                           'type': 'data',
                           'uuid': partition_uuid,
                       }]}]
            assert expect == main.list_devices(args)
        #
        # multipath journal partition
        #
        journal_partition_uuid = "2cc40457-259e-4542-b029-785c7cc37871"
        with patch.multiple(
                main,
                list_all_partitions=lambda names: {disk: [partition]},
                get_partition_uuid=lambda dev: journal_partition_uuid,
                get_partition_type=lambda dev: main.MPATH_JOURNAL_UUID,
                is_partition=lambda dev: True,
        ):
            expect = [{'path': '/dev/' + disk,
                       'partitions': [{
                           'dmcrypt': {},
                           'is_partition': True,
                           'multipath': True,
                           'path': '/dev/' + partition,
                           'ptype': main.MPATH_JOURNAL_UUID,
                           'type': 'journal',
                           'uuid': journal_partition_uuid,
                       }]}]
            assert expect == main.list_devices(args)

    def test_list_dmcrypt(self):
        self.list(main.DMCRYPT_OSD_UUID, main.DMCRYPT_JOURNAL_UUID)
        self.list(main.DMCRYPT_LUKS_OSD_UUID, main.DMCRYPT_LUKS_JOURNAL_UUID)

    def test_list_normal(self):
        self.list(main.OSD_UUID, main.JOURNAL_UUID)

    def list(self, data_ptype, journal_ptype):
        args = main.parse_args(['--verbose', 'list'])
        #
        # a single disk has a data partition and a journal
        # partition and the osd is active
        #
        data_uuid = "56244cf5-83ef-4984-888a-2d8b8e0e04b2"
        disk = "Xda"
        data = "Xda1"
        data_holder = "dm-0"
        journal = "Xda2"
        journal_holder = "dm-0"
        mount_path = '/mount/path'
        fs_type = 'ext4'
        journal_uuid = "7ad5e65a-0ca5-40e4-a896-62a74ca61c55"
        ceph_fsid = "60a2ef70-d99b-4b9b-a83c-8a86e5e60091"
        osd_id = '1234'

        def get_oneliner(path, what):
            if what == 'journal_uuid':
                return journal_uuid
            elif what == 'ceph_fsid':
                return ceph_fsid
            elif what == 'whoami':
                return osd_id
            else:
                raise Exception('unknown ' + what)

        def get_partition_uuid(dev):
            if dev == '/dev/' + data:
                return data_uuid
            elif dev == '/dev/' + journal:
                return journal_uuid
            else:
                raise Exception('unknown ' + dev)

        def get_partition_type(dev):
            if (dev == '/dev/' + data or
                    dev == '/dev/' + data_holder):
                return data_ptype
            elif (dev == '/dev/' + journal or
                    dev == '/dev/' + journal_holder):
                return journal_ptype
            else:
                raise Exception('unknown ' + dev)
        cluster = 'ceph'
        if data_ptype == main.OSD_UUID:
            data_dmcrypt = {}
        elif data_ptype == main.DMCRYPT_OSD_UUID:
            data_dmcrypt = {
                'type': 'plain',
                'holders': [data_holder],
            }
        elif data_ptype == main.DMCRYPT_LUKS_OSD_UUID:
            data_dmcrypt = {
                'type': 'LUKS',
                'holders': [data_holder],
            }
        else:
            raise Exception('unknown ' + data_ptype)

        if journal_ptype == main.JOURNAL_UUID:
            journal_dmcrypt = {}
        elif journal_ptype == main.DMCRYPT_JOURNAL_UUID:
            journal_dmcrypt = {
                'type': 'plain',
                'holders': [journal_holder],
            }
        elif journal_ptype == main.DMCRYPT_LUKS_JOURNAL_UUID:
            journal_dmcrypt = {
                'type': 'LUKS',
                'holders': [journal_holder],
            }
        else:
            raise Exception('unknown ' + journal_ptype)

        if data_dmcrypt:
            def is_held(dev):
                if dev == '/dev/' + data:
                    return [data_holder]
                elif dev == '/dev/' + journal:
                    return [journal_holder]
                else:
                    raise Exception('unknown ' + dev)
        else:
            def is_held(dev):
                return []

        with patch.multiple(
                main,
                list_all_partitions=lambda names: {disk: [data, journal]},
                get_dev_fs=lambda dev: fs_type,
                is_mounted=lambda dev: mount_path,
                get_partition_uuid=get_partition_uuid,
                get_partition_type=get_partition_type,
                find_cluster_by_uuid=lambda ceph_fsid: cluster,
                is_partition=lambda dev: True,
                mount=DEFAULT,
                unmount=DEFAULT,
                get_oneliner=get_oneliner,
                is_held=is_held,
        ):
            expect = [{'path': '/dev/' + disk,
                       'partitions': [{
                           'ceph_fsid': ceph_fsid,
                           'cluster': cluster,
                           'dmcrypt': data_dmcrypt,
                           'fs_type': fs_type,
                           'is_partition': True,
                           'journal_dev': '/dev/' + journal,
                           'journal_uuid': journal_uuid,
                           'mount': mount_path,
                           'path': '/dev/' + data,
                           'ptype': data_ptype,
                           'state': 'active',
                           'type': 'data',
                           'whoami': osd_id,
                           'uuid': data_uuid,
                       }, {
                           'dmcrypt': journal_dmcrypt,
                           'is_partition': True,
                           'journal_for': '/dev/' + data,
                           'path': '/dev/' + journal,
                           'ptype': journal_ptype,
                           'type': 'journal',
                           'uuid': journal_uuid,
                       }]}]
            assert expect == main.list_devices(args)

    def test_list_other(self):
        args = main.parse_args(['list'])
        #
        # not swap, unknown fs type, not mounted, with uuid
        #
        partition_uuid = "56244cf5-83ef-4984-888a-2d8b8e0e04b2"
        partition_type = "e51adfb9-e9fd-4718-9fc1-7a0cb03ea3f4"
        disk = "Xda"
        partition = "Xda1"
        with patch.multiple(
                main,
                list_all_partitions=lambda names: {disk: [partition]},
                get_partition_uuid=lambda dev: partition_uuid,
                get_partition_type=lambda dev: partition_type,
                is_partition=lambda dev: True,
        ):
            expect = [{'path': '/dev/' + disk,
                       'partitions': [{'dmcrypt': {},
                                       'is_partition': True,
                                       'path': '/dev/' + partition,
                                       'ptype': partition_type,
                                       'type': 'other',
                                       'uuid': partition_uuid}]}]
            assert expect == main.list_devices(args)
        #
        # not swap, mounted, ext4 fs type, with uuid
        #
        partition_uuid = "56244cf5-83ef-4984-888a-2d8b8e0e04b2"
        partition_type = "e51adfb9-e9fd-4718-9fc1-7a0cb03ea3f4"
        disk = "Xda"
        partition = "Xda1"
        mount_path = '/mount/path'
        fs_type = 'ext4'
        with patch.multiple(
                main,
                list_all_partitions=lambda names: {disk: [partition]},
                get_dev_fs=lambda dev: fs_type,
                is_mounted=lambda dev: mount_path,
                get_partition_uuid=lambda dev: partition_uuid,
                get_partition_type=lambda dev: partition_type,
                is_partition=lambda dev: True,
        ):
            expect = [{'path': '/dev/' + disk,
                       'partitions': [{
                           'dmcrypt': {},
                           'is_partition': True,
                           'mount': mount_path,
                           'fs_type': fs_type,
                           'path': '/dev/' + partition,
                           'ptype': partition_type,
                           'type': 'other',
                           'uuid': partition_uuid,
                       }]}]
            assert expect == main.list_devices(args)

        #
        # swap, with uuid
        #
        partition_uuid = "56244cf5-83ef-4984-888a-2d8b8e0e04b2"
        partition_type = "e51adfb9-e9fd-4718-9fc1-7a0cb03ea3f4"
        disk = "Xda"
        partition = "Xda1"
        with patch.multiple(
                main,
                list_all_partitions=lambda names: {disk: [partition]},
                is_swap=lambda dev: True,
                get_partition_uuid=lambda dev: partition_uuid,
                get_partition_type=lambda dev: partition_type,
                is_partition=lambda dev: True,
        ):
            expect = [{'path': '/dev/' + disk,
                       'partitions': [{'dmcrypt': {},
                                       'is_partition': True,
                                       'path': '/dev/' + partition,
                                       'ptype': partition_type,
                                       'type': 'swap',
                                       'uuid': partition_uuid}]}]
            assert expect == main.list_devices(args)

        #
        # whole disk
        #
        partition_uuid = "56244cf5-83ef-4984-888a-2d8b8e0e04b2"
        disk = "Xda"
        partition = "Xda1"
        with patch.multiple(
                main,
                list_all_partitions=lambda names: {disk: []},
                is_partition=lambda dev: False,
        ):
            expect = [{'path': '/dev/' + disk,
                       'dmcrypt': {},
                       'is_partition': False,
                       'ptype': 'unknown',
                       'type': 'other'}]
            assert expect == main.list_devices(args)


class TestCephDiskDeactivateAndDestroy(unittest.TestCase):

    def setup_class(self):
        main.setup_logging(verbose=True, log_stdout=False)

    @patch('__builtin__.open')
    def test_main_deactivate(self, mock_open):
        DMCRYPT_LUKS_OSD_UUID = '4fbd7e29-9d25-41b8-afd0-35865ceff05d'
        part_uuid = '0ce28a16-6d5d-11e5-aec3-fa163e5c167b'
        disk = 'sdX'
        #
        # Can not find match device by osd-id
        #
        args = main.parse_args(['deactivate',
                                '--cluster', 'ceph',
                                '--deactivate-by-id', '5566'])
        fake_device = [{'path': '/dev/' + disk,
                        'partitions': [{
                            'path': '/dev/sdX1',
                            'whoami': '-1',
                        }]}]
        with patch.multiple(
                main,
                list_devices=lambda dev: fake_device,
        ):
            main.setup_statedir(main.STATEDIR)
            self.assertRaises(Exception, main.main_deactivate, args)

        #
        # find match device by osd-id, status: OSD_STATUS_IN_DOWN
        # with --mark-out option
        #
        args = main.parse_args(['deactivate',
                                '--cluster', 'ceph',
                                '--deactivate-by-id', '5566',
                                '--mark-out'])
        fake_device = [{'path': '/dev/' + disk,
                        'partitions': [{
                            'ptype': DMCRYPT_LUKS_OSD_UUID,
                            'path': '/dev/sdX1',
                            'whoami': '5566',
                            'mount': '/var/lib/ceph/osd/ceph-5566/',
                            'uuid': part_uuid,
                        }]}]
        with patch.multiple(
                main,
                list_devices=lambda dev: fake_device,
                _check_osd_status=lambda cluster, osd_id: 2,
                _mark_osd_out=lambda cluster, osd_id: True
        ):
            main.setup_statedir(main.STATEDIR)
            main.main_deactivate(args)

        #
        # find match device by device partition, status: OSD_STATUS_IN_DOWN
        #
        args = main.parse_args(['deactivate',
                                '--cluster', 'ceph',
                                '/dev/sdX1'])
        fake_device = [{'path': '/dev/' + disk,
                        'partitions': [{
                            'ptype': DMCRYPT_LUKS_OSD_UUID,
                            'path': '/dev/sdX1',
                            'whoami': '5566',
                            'mount': '/var/lib/ceph/osd/ceph-5566/',
                            'uuid': part_uuid,
                        }]}]
        with patch.multiple(
                main,
                list_devices=lambda dev: fake_device,
                _check_osd_status=lambda cluster, osd_id: 0,
        ):
            main.setup_statedir(main.STATEDIR)
            main.main_deactivate(args)

        #
        # find match device by device partition, status: OSD_STATUS_IN_UP
        # with --mark-out option
        #
        args = main.parse_args(['deactivate',
                                '--cluster', 'ceph',
                                '/dev/sdX1',
                                '--mark-out'])
        fake_device = [{'path': '/dev/' + disk,
                        'partitions': [{
                            'ptype': DMCRYPT_LUKS_OSD_UUID,
                            'path': '/dev/sdX1',
                            'whoami': '5566',
                            'mount': '/var/lib/ceph/osd/ceph-5566/',
                            'uuid': part_uuid,
                        }]}]

        # mock the file open.
        file_opened = io.StringIO()
        file_opened.write(u'deactive')
        mock_open.return_value = file_opened

        with patch.multiple(
                main,
                mock_open,
                list_devices=lambda dev: fake_device,
                _check_osd_status=lambda cluster, osd_id: 3,
                _mark_osd_out=lambda cluster, osd_id: True,
                stop_daemon=lambda cluster, osd_id: True,
                _remove_osd_directory_files=lambda path, cluster: True,
                path_set_context=lambda path: True,
                unmount=lambda path: True,
                dmcrypt_unmap=lambda part_uuid: True,
        ):
            main.setup_statedir(main.STATEDIR)
            main.main_deactivate(args)

        #
        # find match device by osd-id, status: OSD_STATUS_OUT_UP
        #
        args = main.parse_args(['deactivate',
                                '--cluster', 'ceph',
                                '--deactivate-by-id', '5566'])
        fake_device = [{'path': '/dev/' + disk,
                        'partitions': [{
                            'ptype': DMCRYPT_LUKS_OSD_UUID,
                            'path': '/dev/sdX1',
                            'whoami': '5566',
                            'mount': '/var/lib/ceph/osd/ceph-5566/',
                            'uuid': part_uuid,
                        }]}]

        # mock the file open.
        file_opened = io.StringIO()
        file_opened.write(u'deactive')
        mock_open.return_value = file_opened

        with patch.multiple(
                main,
                mock_open,
                list_devices=lambda dev: fake_device,
                _check_osd_status=lambda cluster, osd_id: 1,
                _mark_osd_out=lambda cluster, osd_id: True,
                stop_daemon=lambda cluster, osd_id: True,
                _remove_osd_directory_files=lambda path, cluster: True,
                path_set_context=lambda path: True,
                unmount=lambda path: True,
                dmcrypt_unmap=lambda part_uuid: True,
        ):
            main.setup_statedir(main.STATEDIR)
            main.main_deactivate(args)

    def test_mark_out_out(self):
        def mark_osd_out_fail(osd_id):
            raise main.Error('Could not find osd.%s, is a vaild/exist osd id?'
                             % osd_id)

        with patch.multiple(
                main,
                command=mark_osd_out_fail,
        ):
            self.assertRaises(Exception, main._mark_osd_out, 'ceph', '5566')

    def test_check_osd_status(self):
        #
        # command failure
        #
        with patch.multiple(
                main,
                command=raise_command_error,
        ):
            self.assertRaises(Exception, main._check_osd_status,
                              'ceph', '5566')

        #
        # osd not found
        #

        fake_data = ('{"osds":[{"osd":0,"up":1,"in":1},'
                     '{"osd":1,"up":1,"in":1}]}')

        def return_fake_value(cmd):
            return fake_data, '', 0

        with patch.multiple(
                main,
                command=return_fake_value,
        ):
            self.assertRaises(Exception, main._check_osd_status,
                              'ceph', '5566')

        #
        # successfully
        #

        fake_data = ('{"osds":[{"osd":0,"up":1,"in":1},'
                     '{"osd":5566,"up":1,"in":1}]}')

        def return_fake_value(cmd):
            return fake_data, '', 0

        with patch.multiple(
                main,
                command=return_fake_value,
        ):
            main._check_osd_status('ceph', '5566')

    def test_stop_daemon(self):
        STATEDIR = '/var/lib/ceph'
        cluster = 'ceph'
        osd_id = '5566'

        def stop_daemon_fail(cmd):
            raise Exception('ceph osd stop failed')

        #
        # fail on init type
        #
        with patch('os.path.exists', return_value=False):
            self.assertRaises(Exception, main.stop_daemon, 'ceph', '5566')

        #
        # faile on os path
        #
        with patch('os.path.exists', return_value=Exception):
            self.assertRaises(Exception, main.stop_daemon, 'ceph', '5566')

        #
        # upstart failure
        #
        fake_path = (STATEDIR + '/osd/{cluster}-{osd_id}/upstart').format(
            cluster=cluster, osd_id=osd_id)

        def path_exist(check_path):
            if check_path == fake_path:
                return True
            else:
                False

        patcher = patch('os.path.exists')
        check_path = patcher.start()
        check_path.side_effect = path_exist
        with patch.multiple(
                main,
                check_path,
                command_check_call=stop_daemon_fail,
        ):
            self.assertRaises(Exception, main.stop_daemon, 'ceph', '5566')

        #
        # sysvinit failure
        #
        fake_path = (STATEDIR + '/osd/{cluster}-{osd_id}/sysvinit').format(
            cluster=cluster, osd_id=osd_id)

        def path_exist(check_path):
            if check_path == fake_path:
                return True
            else:
                return False

        patcher = patch('os.path.exists')
        check_path = patcher.start()
        check_path.side_effect = path_exist
        with patch.multiple(
                main,
                check_path,
                which=lambda name: True,
                command_check_call=stop_daemon_fail,
        ):
            self.assertRaises(Exception, main.stop_daemon, 'ceph', '5566')

        #
        # systemd failure
        #
        fake_path = (STATEDIR + '/osd/{cluster}-{osd_id}/systemd').format(
            cluster=cluster, osd_id=osd_id)

        def path_exist(check_path):
            if check_path == fake_path:
                return True
            else:
                False

        def stop_daemon_fail(cmd):
            if 'stop' in cmd:
                raise Exception('ceph osd stop failed')
            else:
                return True

        patcher = patch('os.path.exists')
        check_path = patcher.start()
        check_path.side_effect = path_exist
        with patch.multiple(
                main,
                check_path,
                command_check_call=stop_daemon_fail,
        ):
            self.assertRaises(Exception, main.stop_daemon, 'ceph', '5566')

    def test_remove_osd_directory_files(self):
        cluster = 'ceph'
        mounted_path = 'somewhere'
        fake_path_2 = None
        fake_path_remove_2 = None
        fake_path_remove_init = None

        def handle_path_exist(check_path):
            if check_path == fake_path:
                return True
            elif fake_path_2 and check_path == fake_path_2:
                return True
            else:
                return False

        def handle_path_remove(remove_path):
            if remove_path == fake_path_remove:
                return True
            elif fake_path_remove_2 and remove_path == fake_path_remove_2:
                return True
            elif (fake_path_remove_init and
                  remove_path == fake_path_remove_init):
                return True
            else:
                raise OSError

        #
        # remove ready file failure
        #
        fake_path = os.path.join(mounted_path, 'ready')
        fake_path_remove = os.path.join(mounted_path, 'no_ready')

        patcher_exist = patch('os.path.exists')
        patcher_remove = patch('os.remove')
        path_exist = patcher_exist.start()
        path_remove = patcher_remove.start()
        path_exist.side_effect = handle_path_exist
        path_remove.side_effect = handle_path_remove
        with patch.multiple(
                main,
                path_exist,
                path_remove,
                get_conf=lambda cluster, **kwargs: True,
        ):
            self.assertRaises(Exception, main._remove_osd_directory_files,
                              'somewhere', cluster)

        #
        # remove active fil failure
        #
        fake_path = os.path.join(mounted_path, 'ready')
        fake_path_2 = os.path.join(mounted_path, 'active')
        fake_path_remove = os.path.join(mounted_path, 'ready')
        fake_path_remove_2 = os.path.join(mounted_path, 'no_active')

        patcher_exist = patch('os.path.exists')
        patcher_remove = patch('os.remove')
        path_exist = patcher_exist.start()
        path_remove = patcher_remove.start()
        path_exist.side_effect = handle_path_exist
        path_remove.side_effect = handle_path_remove
        with patch.multiple(
                main,
                path_exist,
                path_remove,
                get_conf=lambda cluster, **kwargs: True,
        ):
            self.assertRaises(Exception, main._remove_osd_directory_files,
                              'somewhere', cluster)

        #
        # conf_val is None and remove init file failure
        #
        fake_path = os.path.join(mounted_path, 'ready')
        fake_path_2 = os.path.join(mounted_path, 'active')
        fake_path_remove = os.path.join(mounted_path, 'ready')
        fake_path_remove_2 = os.path.join(mounted_path, 'active')
        fake_path_remove_init = os.path.join(mounted_path, 'init_failure')

        patcher_exist = patch('os.path.exists')
        patcher_remove = patch('os.remove')
        path_exist = patcher_exist.start()
        path_remove = patcher_remove.start()
        path_exist.side_effect = handle_path_exist
        path_remove.side_effect = handle_path_remove
        with patch.multiple(
                main,
                path_exist,
                path_remove,
                get_conf=lambda cluster, **kwargs: None,
                init_get=lambda: 'upstart',
        ):
            self.assertRaises(Exception, main._remove_osd_directory_files,
                              'somewhere', cluster)

        #
        # already remove `ready`, `active` and remove init file successfully
        #
        fake_path = os.path.join(mounted_path, 'no_ready')
        fake_path_2 = os.path.join(mounted_path, 'no_active')
        fake_path_remove = os.path.join(mounted_path, 'upstart')

        patcher_exist = patch('os.path.exists')
        patcher_remove = patch('os.remove')
        path_exist = patcher_exist.start()
        path_remove = patcher_remove.start()
        path_exist.side_effect = handle_path_exist
        path_remove.side_effect = handle_path_remove
        with patch.multiple(
                main,
                path_exist,
                path_remove,
                get_conf=lambda cluster, **kwargs: 'upstart',
        ):
            main._remove_osd_directory_files('somewhere', cluster)

    def test_path_set_context(self):
        path = '/somewhere'
        with patch.multiple(
                main,
                get_ceph_user=lambda **kwargs: 'ceph',
        ):
            main.path_set_context(path)

    def test_mount(self):
        #
        # None to mount
        #
        dev = None
        fs_type = 'ext4'
        option = ''
        self.assertRaises(Exception, main.mount, dev, fs_type, option)

        #
        # fstype undefine
        #
        dev = '/dev/Xda1'
        fs_type = None
        option = ''
        self.assertRaises(Exception, main.mount, dev, fs_type, option)

        #
        # mount failure
        #
        dev = '/dev/Xda1'
        fstype = 'ext4'
        options = ''
        with patch('tempfile.mkdtemp', return_value='/mnt'):
            self.assertRaises(Exception, main.mount, dev, fstype, options)

        #
        # mount successfully
        #
        def create_temp_directory(*args, **kwargs):
            return '/mnt'

        dev = '/dev/Xda1'
        fstype = 'ext4'
        options = ''
        patcher = patch('tempfile.mkdtemp')
        create_tmpdir = patcher.start()
        create_tmpdir.side_effect = create_temp_directory
        with patch.multiple(
                main,
                create_tmpdir,
                command_check_call=lambda cmd: True,
        ):
            main.mount(dev, fstype, options)

    def test_umount(self):
        #
        # umount failure
        #
        path = '/somewhere'
        self.assertRaises(Exception, main.unmount, path)

        #
        # umount successfully
        #
        def remove_directory_successfully(path):
            return True

        path = '/somewhere'
        patcher = patch('os.rmdir')
        rm_directory = patcher.start()
        rm_directory.side_effect = remove_directory_successfully
        with patch.multiple(
                main,
                rm_directory,
                command_check_call=lambda cmd: True,
        ):
            main.unmount(path)

    def test_main_destroy(self):
        DMCRYPT_OSD_UUID = '4fbd7e29-9d25-41b8-afd0-5ec00ceff05d'
        DMCRYPT_LUKS_OSD_UUID = '4fbd7e29-9d25-41b8-afd0-35865ceff05d'
        OSD_UUID = '4fbd7e29-9d25-41b8-afd0-062c0ceff05d'
        MPATH_OSD_UUID = '4fbd7e29-8ae0-4982-bf9d-5a8d867af560'
        part_uuid = '0ce28a16-6d5d-11e5-aec3-fa163e5c167b'
        journal_uuid = "7ad5e65a-0ca5-40e4-a896-62a74ca61c55"
        mount_5566 = '/var/lib/ceph/osd/ceph-5566/'
        ptype_0 = '00000000-0000-0000-0000-000000000000'

        fake_devices_normal = [{'path': '/dev/sdY',
                                'partitions': [{
                                    'dmcrypt': {},
                                    'ptype': OSD_UUID,
                                    'path': '/dev/sdY1',
                                    'whoami': '5566',
                                    'mount': mount_5566,
                                    'uuid': part_uuid,
                                    'journal_uuid': journal_uuid}]},
                               {'path': '/dev/sdX',
                                'partitions': [{
                                    'dmcrypt': {},
                                    'ptype': MPATH_OSD_UUID,
                                    'path': '/dev/sdX1',
                                    'whoami': '7788',
                                    'mount': '/var/lib/ceph/osd/ceph-7788/',
                                    'uuid': part_uuid,
                                    'journal_uuid': journal_uuid}]}]
        fake_devices_dmcrypt_unmap = [{'path': '/dev/sdY',
                                       'partitions': [{
                                           'dmcrypt': {
                                               'holders': '',
                                               'type': type,
                                           },
                                           'ptype': DMCRYPT_OSD_UUID,
                                           'path': '/dev/sdX1',
                                           'whoami': '5566',
                                           'mount': mount_5566,
                                           'uuid': part_uuid,
                                           'journal_uuid': journal_uuid}]}]
        fake_devices_dmcrypt_luk_unmap = [{'path': '/dev/sdY',
                                           'partitions': [{
                                               'dmcrypt': {
                                                   'holders': '',
                                                   'type': type,
                                               },
                                               'ptype': DMCRYPT_LUKS_OSD_UUID,
                                               'path': '/dev/sdX1',
                                               'whoami': '5566',
                                               'mount': mount_5566,
                                               'uuid': part_uuid,
                                               'journal_uuid': journal_uuid}]}]
        fake_devices_dmcrypt_unknow = [{'path': '/dev/sdY',
                                        'partitions': [{
                                            'dmcrypt': {
                                                'holders': '',
                                                'type': type,
                                            },
                                            'ptype': ptype_0,
                                            'path': '/dev/sdX1',
                                            'whoami': '5566',
                                            'mount': mount_5566,
                                            'uuid': part_uuid,
                                            'journal_uuid': journal_uuid}]}]
        fake_devices_dmcrypt_map = [{'dmcrypt': {'holders': 'dm_0',
                                                 'type': type},
                                     'ptype': DMCRYPT_OSD_UUID,
                                     'path': '/dev/sdX1',
                                     'whoami': '5566',
                                     'mount': mount_5566,
                                     'uuid': part_uuid,
                                     'journal_uuid': journal_uuid}]

        def list_devices_return(dev):
            if dev == []:
                return fake_devices_normal

        #
        # input device is not the device partition
        #
        args = main.parse_args(['destroy', '--cluster', 'ceph', '/dev/sdX'])
        with patch.multiple(
                main,
                is_partition=lambda path: False,
        ):
            self.assertRaises(Exception, main.main_destroy, args)

        #
        # skip the redundent devices and not found by dev
        #
        args = main.parse_args(['destroy', '--cluster', 'ceph', '/dev/sdZ1'])
        with patch.multiple(
                main,
                is_partition=lambda path: True,
                list_devices=list_devices_return,
        ):
            self.assertRaises(Exception, main.main_destroy, args)

        #
        # skip the redundent devices and not found by osd-id
        #
        args = main.parse_args(['destroy', '--cluster', 'ceph',
                                '--destroy-by-id', '1234'])
        with patch.multiple(
                main,
                is_partition=lambda path: True,
                list_devices=list_devices_return,
        ):
            self.assertRaises(Exception, main.main_destroy, args)

        #
        # skip the redundent devices and found by dev
        #
        args = main.parse_args(['destroy', '--cluster',
                                'ceph', '/dev/sdY1', '--zap'])
        with patch.multiple(
                main,
                is_partition=lambda path: True,
                list_devices=list_devices_return,
                get_partition_base=lambda dev_path: '/dev/sdY',
                _check_osd_status=lambda cluster, osd_id: 0,
                _remove_from_crush_map=lambda cluster, osd_id: True,
                _delete_osd_auth_key=lambda cluster, osd_id: True,
                _deallocate_osd_id=lambda cluster, osd_id: True,
                zap=lambda dev: True
        ):
            main.main_destroy(args)

        #
        # skip the redundent devices and found by osd-id
        # with active status and MPATH_OSD
        #
        args = main.parse_args(['destroy', '--cluster', 'ceph',
                                '--destroy-by-id', '7788'])
        with patch.multiple(
                main,
                is_partition=lambda path: True,
                list_devices=list_devices_return,
                get_partition_base_mpath=lambda dev_path: '/dev/sdX',
                _check_osd_status=lambda cluster, osd_id: 1,
        ):
            self.assertRaises(Exception, main.main_destroy, args)

        #
        # skip the redundent devices and found by dev
        # with dmcrypt (plain)
        #
        args = main.parse_args(['destroy', '--cluster', 'ceph',
                                '/dev/sdX1', '--zap'])

        def list_devices_return(dev):
            if dev == []:
                return fake_devices_dmcrypt_unmap
            elif dev == ['/dev/sdX1']:
                return fake_devices_dmcrypt_map

        with patch.multiple(
                main,
                is_partition=lambda path: True,
                list_devices=list_devices_return,
                get_dmcrypt_key_path=lambda *args, **kwargs: True,
                dmcrypt_map=lambda *args, **kwargs: True,
                dmcrypt_unmap=lambda part_uuid: True,
                get_partition_base=lambda dev_path: '/dev/sdX',
                _check_osd_status=lambda cluster, osd_id: 0,
                _remove_from_crush_map=lambda cluster, osd_id: True,
                _delete_osd_auth_key=lambda cluster, osd_id: True,
                _deallocate_osd_id=lambda cluster, osd_id: True,
                zap=lambda dev: True
        ):
            main.main_destroy(args)

        #
        # skip the redundent devices and found by osd-id
        # with dmcrypt (luk) and status: active
        #
        args = main.parse_args(['destroy', '--cluster', 'ceph',
                                '--destroy-by-id', '5566'])

        def list_devices_return(dev):
            if dev == []:
                return fake_devices_dmcrypt_luk_unmap
            elif dev == ['/dev/sdX1']:
                return fake_devices_dmcrypt_map

        with patch.multiple(
                main,
                is_partition=lambda path: True,
                list_devices=list_devices_return,
                get_dmcrypt_key_path=lambda *args, **kwargs: True,
                dmcrypt_map=lambda *args, **kwargs: True,
                dmcrypt_unmap=lambda part_uuid: True,
                get_partition_base=lambda dev_path: '/dev/sdX',
                _check_osd_status=lambda cluster, osd_id: 1,
        ):
            self.assertRaises(Exception, main.main_destroy, args)

        #
        # skip the redundent devices and found by osd-id
        # with unknow dmcrypt type
        #
        args = main.parse_args(['destroy', '--cluster', 'ceph',
                                '--destroy-by-id', '5566'])

        def list_devices_return(dev):
            if dev == []:
                return fake_devices_dmcrypt_unknow

        with patch.multiple(
                main,
                is_partition=lambda path: True,
                list_devices=list_devices_return,
        ):
            self.assertRaises(Exception, main.main_destroy, args)

    def test_remove_from_crush_map_fail(self):
        cluster = 'ceph'
        osd_id = '5566'
        with patch.multiple(
                main,
                command=raise_command_error
        ):
            self.assertRaises(Exception, main._remove_from_crush_map,
                              cluster, osd_id)

    def test_delete_osd_auth_key_fail(self):
        cluster = 'ceph'
        osd_id = '5566'
        with patch.multiple(
                main,
                command=raise_command_error
        ):
            self.assertRaises(Exception, main._delete_osd_auth_key,
                              cluster, osd_id)

    def test_deallocate_osd_id_fail(self):
        cluster = 'ceph'
        osd_id = '5566'
        with patch.multiple(
                main,
                command=raise_command_error
        ):
            self.assertRaises(Exception, main._deallocate_osd_id,
                              cluster, osd_id)


def raise_command_error(*args):
    e = subprocess.CalledProcessError('aaa', 'bbb', 'ccc')
    raise e


def path_exists(target_paths=None):
    """
    A quick helper that enforces a check for the existence of a path. Since we
    are dealing with fakes, we allow to pass in a list of paths that are OK to
    return True, otherwise return False.
    """
    target_paths = target_paths or []

    def exists(path):
        return path in target_paths
    return exists
