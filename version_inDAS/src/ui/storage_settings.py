"""Independent joint-storage draft dialog hosted by View."""
from pathlib import Path
from .view_settings import ViewSettingsDialog


class StorageSettingsDialog(ViewSettingsDialog):
    dialog_title = '联合存储参数设置'
    fields = (('联合存储', (
        ('启用联合存储', 'tab3_joint_storage_toggle_btn'),
        ('存储目录', 'tab3_storage_path_edit'),
        ('每文件间隔 (s)', 'tab3_storage_interval_spin'),
        ('FIP 降采样', 'tab3_joint_fip_downsample_spin'),
        ('缓存 (s)', 'tab3_cache_seconds_spin'),
        ('文件格式', 'tab3_joint_format_combo'),
        ('H5 压缩', 'tab3_joint_h5_compress_combo'),
    )),)

    def __init__(self, window):
        super().__init__(window)
        if self.running():
            for name, editor in self.editors.items():
                if name != 'tab3_joint_storage_toggle_btn': editor.setEnabled(False)
        self.editors['tab3_storage_path_edit'].setPlaceholderText('联合 FIP/eDAS 文件保存目录')

    def running(self):
        return self.host.monitoring_active or self.host._edas_monitoring_active or self.host.edas_panel.acquisition_running()

    def validate(self):
        if self.editors['tab3_joint_storage_toggle_btn'].isChecked():
            text = self.editors['tab3_storage_path_edit'].text().strip()
            if not text: return '启用联合存储时必须设置存储目录。'
            try:
                if Path(text).is_file(): return '存储目录不能是已有文件。'
            except (ValueError, OSError): return '存储目录格式无效或无法访问。'
        if self.running():
            for name, editor in self.editors.items():
                if name != 'tab3_joint_storage_toggle_btn' and self.value(editor) != self.value(getattr(self.host, name)):
                    return '请先停止采集和 FIP 通信，再修改存储路径、格式和分段参数。'
        return ''
