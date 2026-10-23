"""View draft settings and ordered/cancellable joint acquisition regression."""
import json
import time
import unittest
import numpy as np
from unittest.mock import patch
from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtTest import QTest, QSignalSpy
from PyQt5.QtWidgets import QGroupBox

import test_acquisition_settings as fixtures
from ui.view_settings import ViewSettingsDialog, VIEW_FIELDS


class ViewWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.AcquisitionSettingsTests.setUpClass()
        cls.helper = fixtures.AcquisitionSettingsTests()
        cls.app = cls.helper.app

    def test_joint_start_waits_one_second_and_stop_stops_both(self):
        with self.helper.window() as (w, folder):
            events = []
            def fip_start():
                events.append(('fip', time.monotonic()))
                w.set_fip_monitoring_active(True)
            def das_start():
                events.append(('das', time.monotonic()))
                w.set_tab3_monitoring_active(True)
            w.start_monitoring.connect(fip_start)
            w.tab3_start_requested.connect(das_start)
            w.tab3_stop_requested.connect(lambda: events.append(('das_stop', time.monotonic())))
            w.stop_monitoring.connect(lambda: events.append(('fip_stop', time.monotonic())))
            w.data_comm_both_btn.click()
            w.data_comm_both_btn.click()
            self.assertEqual([name for name, _ in events], ['fip'])
            QTest.qWait(1100)
            self.assertEqual([name for name, _ in events], ['fip'])
            self.assertEqual(w._joint_start_stage, 'wait_fip_connection')
            self.assertIn('监听中', w.conn_status_label.text())
            connected_at = time.monotonic()
            w.update_connection_status(True, 'Connected')
            QTest.qWait(400)
            self.assertEqual([name for name, _ in events], ['fip'])
            QTest.qWait(750)
            self.assertEqual([name for name, _ in events], ['fip', 'das'])
            self.assertGreaterEqual(events[1][1] - connected_at, 1.0)
            self.assertFalse(w.data_comm_both_btn.isEnabled())
            w.joint_stop_btn.click()
            self.assertEqual([name for name, _ in events], ['fip', 'das', 'das_stop', 'fip_stop'])
            self.assertFalse(w.monitoring_active)
            self.assertFalse(w._edas_monitoring_active)
            self.assertTrue(w.data_comm_both_btn.isEnabled())

    def test_joint_stop_cancels_pending_start_and_fip_failure_does_not_start_das(self):
        with self.helper.window() as (w, folder):
            das = QSignalSpy(w.tab3_start_requested)
            w.data_comm_both_btn.click()
            self.assertEqual(w._joint_start_stage, 'wait_fip_connection')
            self.assertFalse(w._joint_start_timer.isActive())
            w.joint_stop_btn.click()
            QTest.qWait(1100)
            self.assertEqual(len(das), 0)
            self.assertFalse(w._joint_start_timer.isActive())
            w.start_monitoring.connect(lambda: w.set_fip_monitoring_active(False))
            w.data_comm_both_btn.click()
            self.assertFalse(w._joint_start_timer.isActive())
            self.assertIn('失败', w.joint_state_label.text())
            self.assertEqual(len(das), 0)

    def test_each_partial_running_state_restarts_both_with_two_delays(self):
        for active_source in ('fip', 'das'):
            with self.subTest(active_source=active_source), self.helper.window() as (w, folder):
                events = []
                def record(name): events.append((name, time.monotonic()))
                def fip_start():
                    record('fip')
                    w.set_fip_monitoring_active(True)
                def das_start():
                    record('das')
                    w.set_tab3_monitoring_active(True)
                def fip_stop():
                    record('fip_stop')
                    w.set_fip_monitoring_active(False)
                def das_stop():
                    record('das_stop')
                    w.set_tab3_monitoring_active(False)
                w.start_monitoring.connect(fip_start)
                w.tab3_start_requested.connect(das_start)
                w.stop_monitoring.connect(fip_stop)
                w.tab3_stop_requested.connect(das_stop)
                w.set_fip_monitoring_active(active_source == 'fip')
                w.set_tab3_monitoring_active(active_source == 'das')
                w.data_comm_both_btn.click()
                w._toggle_both_communication()  # Repeated requests cannot skip the waits.
                self.assertEqual([e[0] for e in events], ['das_stop', 'fip_stop'])
                QTest.qWait(450)
                self.assertEqual(len(events), 2)
                QTest.qWait(650)
                self.assertEqual([e[0] for e in events], ['das_stop', 'fip_stop', 'fip'])
                w.update_connection_status(True, 'Connected')
                QTest.qWait(1050)
                self.assertEqual([e[0] for e in events], ['das_stop', 'fip_stop', 'fip', 'das'])
                self.assertGreaterEqual(events[2][1] - events[1][1], 1.0)
                self.assertGreaterEqual(events[3][1] - events[2][1], 1.0)
                w._toggle_both_communication()
                self.assertEqual(len(events), 4)  # Both active is an exact no-op.

    def test_stop_or_close_during_restart_wait_cancels_all_stages(self):
        for action in ('stop', 'close'):
            with self.subTest(action=action), self.helper.window() as (w, folder):
                fip = QSignalSpy(w.start_monitoring)
                das = QSignalSpy(w.tab3_start_requested)
                w.set_tab3_monitoring_active(True)
                w.data_comm_both_btn.click()
                self.assertEqual(w._joint_start_stage, 'restart_fip')
                if action == 'stop': w.joint_stop_btn.click()
                else: w.close()
                QTest.qWait(1150)
                self.assertEqual(len(fip), 0)
                self.assertEqual(len(das), 0)
                self.assertFalse(w._joint_start_timer.isActive())

    def test_independent_tab_buttons_do_not_start_the_other_source(self):
        with self.helper.window() as (w, folder):
            fip = QSignalSpy(w.start_monitoring)
            das = QSignalSpy(w.tab3_start_requested)
            w.edas_panel.start_btn.click()
            self.assertEqual(len(das), 1)
            self.assertEqual(len(fip), 0)
            w.set_tab3_monitoring_active(True)
            w.start_stop_btn.click()
            self.assertEqual(len(fip), 1)
            self.assertEqual(len(das), 1)
            self.assertTrue(w._edas_monitoring_active)
            w.start_stop_btn.click()
            self.assertTrue(w._edas_monitoring_active)

    def test_fip_stop_and_window_close_cancel_pending_das_start(self):
        with self.helper.window() as (w, folder):
            das = QSignalSpy(w.tab3_start_requested)
            w.data_comm_both_btn.click()
            w.set_fip_monitoring_active(False)
            self.assertFalse(w._joint_start_timer.isActive())
            w.data_comm_both_btn.click()
            w.close()
            QTest.qWait(1100)
            self.assertEqual(len(das), 0)

    def test_view_panel_only_shows_curve_group_settings_and_bottom_start_stop(self):
        with self.helper.window() as (w, folder):
            w.tab_widget.setCurrentIndex(0)
            self.app.processEvents()
            visible = [g.title() for g in w.param_widget.findChildren(QGroupBox) if g.isVisible()]
            self.assertEqual(visible, ['曲线选择', '运行状态', '联合采集'])
            self.assertTrue(all(g.isHidden() for g in w._view_model_groups))
            self.assertTrue(w.param_widget.isAncestorOf(w.data_comm_both_btn))
            self.assertTrue(w.tab3_start_stop_btn.isHidden())
            self.assertEqual(w.data_comm_both_btn.text(), 'Start')
            self.assertEqual(w.joint_stop_btn.text(), 'Stop')
            self.assertGreater(w.joint_control_group.y(), w.view_settings_btn.y())

    def test_joint_start_reaches_simulated_acquisition_and_stop_drains_it(self):
        from alignment import AlignedSessionCoordinator
        from das import EDASManager
        from edas_hardware.acquisition_settings import AcquisitionSettingsDialog
        with self.helper.window() as (w, folder):
            p = w.edas_panel
            draft = AcquisitionSettingsDialog(p, w)
            for key, value in {'basic.scan_rate': 1000, 'basic.point_num_per_scan': 2048,
                               'phase_demod.merge_point_num': 4, 'display.length_load_s': .1,
                               'display.length_plot_s': .4, 'comm.channel_start': 0,
                               'comm.channel_end': 9}.items():
                self.helper.select(draft, key, value)
            self.assertTrue(draft.apply_settings(), draft.error_label.text())
            p.save_enable_check.setChecked(False)
            w.tab3_joint_storage_toggle_btn.setChecked(False)
            coordinator = AlignedSessionCoordinator()
            coordinator.start_session()
            manager = EDASManager(w, coordinator)
            def fip_start():
                coordinator.update_online_state('fip', True)
                w.set_fip_monitoring_active(True)
            def das_start(): w.set_tab3_monitoring_active(manager.start())
            def das_stop():
                manager.stop()
                w.set_tab3_monitoring_active(False)
            w.start_monitoring.connect(fip_start)
            w.tab3_start_requested.connect(das_start)
            w.tab3_stop_requested.connect(das_stop)
            w.stop_monitoring.connect(lambda: w.set_fip_monitoring_active(False))
            try:
                w.data_comm_both_btn.click()
                self.assertIsNone(p.acq_thread)
                w.update_connection_status(True, 'Connected')
                QTest.qWait(1900)
                self.assertTrue(p.acq_thread.isRunning())
                self.assertTrue(w._edas_monitoring_active)
                self.assertGreater(p.time_space_widget._valid_block_count, 0)
                w.joint_stop_btn.click()
                self.assertTrue(p.acq_thread is None or not p.acq_thread.isRunning())
                self.assertFalse(w.monitoring_active)
                self.assertFalse(w._edas_monitoring_active)
            finally:
                manager.stop()

    def test_view_outside_click_discards_draft(self):
        with self.helper.window() as (w, folder):
            w.tab_widget.setCurrentIndex(0)
            before = w.get_current_config()
            w.open_view_settings()
            d = w._view_settings_dialog
            d.move(w.mapToGlobal(QPoint(600, 300)))
            self.app.processEvents()
            d.editors['time_display_duration_spin'].setValue(9)
            bar = w.tab_widget.tabBar()
            self.assertFalse(d.frameGeometry().contains(bar.mapToGlobal(QPoint(5, 5))))
            QTest.mouseClick(bar, Qt.LeftButton, pos=QPoint(5, 5))
            self.app.processEvents()
            self.assertIsNone(w._view_settings_dialog)
            self.assertEqual(w.get_current_config(), before)

    def test_view_draft_cancel_validation_apply_and_persistence_preserve_running_state(self):
        with self.helper.window() as (w, folder):
            w._save_configuration()
            saved = (folder / 'gui.json').read_bytes()
            original = w.get_current_config()
            d = ViewSettingsDialog(w)
            self.assertEqual(d.tabs.count(), 6)
            self.assertEqual(len(d.editors), sum(len(fields) for _, fields in VIEW_FIELDS))
            d.editors['time_display_duration_spin'].setValue(2.5)
            d.reject()
            self.assertEqual(w.get_current_config(), original)
            self.assertEqual((folder / 'gui.json').read_bytes(), saved)
            d = ViewSettingsDialog(w)
            d.editors['view_axis_enable_check'].setChecked(True)
            d.editors['view_x_range_edit'].setText('invalid')
            self.assertFalse(d.apply_settings())
            self.assertEqual(w.get_current_config(), original)
            d.reject()
            d = ViewSettingsDialog(w)
            d.editors['time_display_duration_spin'].setValue(2.5)
            d.editors['fip_filter_enable_check'].setChecked(True)
            d.editors['fip_filter_range_edit'].setText('100-200')
            d.editors['tab3_das_filter_enable_check'].setChecked(True)
            d.editors['tab3_das_filter_range_edit'].setText('40-80')
            d.editors['space_time_plot_btn'].setChecked(False)
            duration = QSignalSpy(w.time_settings_changed)
            filtering = QSignalSpy(w.filter_settings_changed)
            w.set_fip_monitoring_active(True)
            w.set_tab3_monitoring_active(True)
            self.assertTrue(d.apply_settings(), d.error_label.text())
            self.assertEqual(w.time_display_duration_spin.value(), 2.5)
            self.assertEqual(w.get_fip_filter_settings()['range'], '100-200')
            self.assertEqual(w.get_tab3_das_filter_settings()['range'], '40-80')
            self.assertFalse(w.space_time_plot_btn.isChecked())
            self.assertTrue(w.monitoring_active)
            self.assertTrue(w._edas_monitoring_active)
            self.assertGreater(len(duration), 0)
            self.assertGreater(len(filtering), 0)
            persisted = json.loads((folder / 'gui.json').read_text(encoding='utf-8'))['config']
            self.assertEqual(persisted, json.loads(json.dumps(w.get_current_config())))

    def test_disconnect_during_countdown_requires_new_full_delay(self):
        with self.helper.window() as (w, folder):
            das = QSignalSpy(w.tab3_start_requested)
            w.data_comm_both_btn.click()
            w.update_connection_status(True,'Connected')
            QTest.qWait(600)
            w.update_connection_status(False,'Disconnected')
            QTest.qWait(600)
            self.assertEqual(len(das),0)
            self.assertEqual(w._joint_start_stage,'wait_fip_connection')
            w.update_connection_status(True,'Connected again')
            QTest.qWait(600)
            # Duplicate connection notification must not restart the timer.
            w.update_connection_status(True,'Connected again')
            self.assertEqual(len(das),0)
            QTest.qWait(450)
            self.assertEqual(len(das),1)
            w.joint_stop_btn.click()
            w.update_connection_status(True,'Late connection')
            QTest.qWait(1050)
            self.assertEqual(len(das),1)

    def test_real_client_connection_without_data_starts_edas_then_first_packet_updates_state(self):
        import socket,struct
        from fip.tcp_server import OptimizedTCPServer
        with self.helper.window() as (w,folder):
            server=OptimizedTCPServer('127.0.0.1',0)
            self.addCleanup(server.stop_server)
            server.connection_status.connect(w.update_connection_status)
            server.data_received.connect(lambda packet:w.record_fip_packet_receive(packet.comm_count,time.time()))
            w.start_monitoring.connect(lambda:(server.start_server(),w.set_fip_monitoring_active(True)))
            w.stop_monitoring.connect(lambda:(server.stop_server(),w.set_fip_monitoring_active(False)))
            starts=[]
            w.tab3_start_requested.connect(lambda:(starts.append(time.monotonic()),w.set_tab3_monitoring_active(True)))
            w.data_comm_both_btn.click()
            # Waiting for the operator is normal, including beyond data-health grace.
            w._fip_requested_at=time.monotonic()-60
            w._refresh_dashboard()
            self.assertEqual(w.runtime_lights['fip'].property('state'),'yellow')
            self.assertIn('监听中',w.runtime_labels['fip'].text())
            QTest.qWait(1100)
            self.assertEqual(starts,[])
            client=socket.create_connection(server.server_socket.getsockname(),timeout=2)
            connected_at=time.monotonic()
            try:
                QTest.qWait(300)
                self.assertTrue(w._fip_connected)
                self.assertEqual(w.conn_status_label.text(),'已连接')
                self.assertEqual(starts,[])
                QTest.qWait(850)
                self.assertEqual(len(starts),1)
                self.assertGreaterEqual(starts[0]-connected_at,1.)
                self.assertEqual(w.conn_status_label.text(),'等待触发')
                body=np.array([1,2,3,4],dtype='>i8').tobytes()
                client.sendall(struct.pack('>II',0,len(body))+body)
                QTest.qWait(200)
                self.assertEqual(w.conn_status_label.text(),'已收数据')
                w._refresh_dashboard()
                self.assertEqual(w.runtime_lights['fip'].property('state'),'green')
            finally:
                w.joint_stop_btn.click();client.close()

    def test_asynchronous_packets_never_push_other_trace_outside_window(self):
        for leading in ('fip','das'):
            with self.subTest(leading=leading), self.helper.window() as (w, folder):
                w.tab_widget.setCurrentIndex(0)
                w.tab3_curve1_combo.setCurrentText('FIP1')
                w.tab3_curve2_combo.setCurrentText('DAS Channel')
                w.time_display_duration_spin.setValue(1)
                w._tab3_fip_plot_min_interval_seconds = 0
                w._tab3_das_plot_min_interval_seconds = 0
                def feed(source, seq):
                    values = np.sin(np.arange(100)*.1)
                    if source == 'fip':
                        w.update_tab3_fip_curve(seq,values,100,values_by_sensor={1:values})
                    else:
                        w.update_tab3_plot_payload({'header':{'comm_count':seq,'packet_duration_seconds':1},
                            'curve2_das_time':seq+np.arange(100)/100.,'curve2_das_values':values})
                feed('fip',0); feed('das',0); w._render_view_plots()
                previous = w.tab3_curve1_plot.viewRange()[0]
                feed(leading,1); w._render_view_plots()
                np.testing.assert_allclose(w.tab3_curve1_plot.viewRange()[0],previous)
                self.assertFalse(w.view_alignment_notice.isHidden())
                feed('das' if leading=='fip' else 'fip',1); w._render_view_plots()
                self.assertTrue(w.view_alignment_notice.isHidden())
                lo,hi=w.tab3_curve1_plot.viewRange()[0]
                for item in (w.tab3_curve1_fip_curve,w.tab3_curve2_das_curve):
                    x,y=item.getData()
                    self.assertTrue(np.count_nonzero((x>=lo)&(x<=hi))>1)
                # Re-rendering an unchanged pair must not accumulate duplicates.
                key=(1,1); count=sum(len(a) for a in w._fip_curve_rolling[key]['times'])
                w._render_view_plots()
                self.assertEqual(sum(len(a) for a in w._fip_curve_rolling[key]['times']),count)

    def test_tcp_stop_delivers_received_qt_tail_before_storage_drain(self):
        import socket,struct,threading
        from types import SimpleNamespace
        from fip.tcp_server import OptimizedTCPServer
        from main import PCCPMonitorApp
        server=OptimizedTCPServer('127.0.0.1',0)
        received=[]; arrived=threading.Event()
        server.data_received.connect(lambda packet: received.append(packet.comm_count))
        server.data_received.connect(lambda packet: arrived.set(),Qt.DirectConnection)
        self.assertTrue(server.start_server())
        client=socket.create_connection(server.server_socket.getsockname(),timeout=2)
        try:
            body=np.array([1,2,3,4],dtype='>i8').tobytes()
            client.sendall(struct.pack('>II',0,len(body))+body)
            self.assertTrue(arrived.wait(2))
            self.assertEqual(received,[])
            PCCPMonitorApp._stop_fip_ingress(SimpleNamespace(tcp_server=server))
            self.assertEqual(received,[0])
            self.assertFalse(server._server_thread.is_alive())
        finally:
            client.close();server.stop_server()

    def test_global_font_default_migration_and_both_tabs_follow_setting(self):
        with self.helper.window() as (w, folder):
            self.assertEqual(w.setting_gui_font_spin.value(), 10)
            self.assertEqual(w.setting_plot_font_spin.value(), 12)
            w._restore_global_display_settings({'setting': {'gui_font_pt': 8, 'tick_font_pt': 11, 'axis_label_px': 9}})
            self.assertEqual(w.setting_gui_font_spin.value(), 10)
            self.assertEqual(w.setting_plot_font_spin.value(), 12)
            for size in (12, 16):
                w.setting_plot_font_spin.setValue(size)
                w._apply_global_display_runtime_settings()
                w.edas_panel._set_time_plot_bottom_label('Time (s)')
                for plot in w._iter_plot_widgets():
                    for side in ('left', 'bottom', 'top', 'right'):
                        axis = plot.getAxis(side)
                        self.assertEqual(axis.style['tickFont'].pointSize(), size)
                        self.assertEqual(axis.style['tickFont'].family(), 'Times New Roman')
                        self.assertEqual(axis.labelStyle['font-size'], f'{size}pt')
                for histogram in (w.tab3_space_time_histogram, w.edas_panel.time_space_widget.histogram_widget):
                    self.assertEqual(histogram.item.axis.style['tickFont'].pointSize(), size)
            self.assertEqual(w.font().pointSize(), 10)
            self.assertEqual(w.edas_panel.font().pointSize(), 10)
            self.assertIn('font-size: 12pt', w.data_comm_both_btn.styleSheet())
            self.assertIn('font-size: 12pt', w.edas_panel.start_btn.styleSheet())
            setting = w.get_global_display_settings()
            self.assertEqual(setting['font_schema_version'], 2)
            self.assertEqual(setting['plot_font_pt'], 16)
            w.setting_plot_font_spin.setValue(12)
            w._restore_global_display_settings({'setting': setting})
            self.assertEqual(w.setting_plot_font_spin.value(), 16)


if __name__ == '__main__': unittest.main()
