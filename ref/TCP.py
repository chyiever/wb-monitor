from socket import *
import struct
import time
from PyQt6.QtCore import QThread, pyqtSignal, QTimer
import numpy as np

class TCPDataReceiver(QThread):
    """TCP数据接收线程 - 直接解析二进制双精度浮点数"""
    rawDataReady = pyqtSignal(np.ndarray)  # 发射numpy数组信号

    def __init__(self, queue_acquir):
        super().__init__()
        self.queue_acquir = queue_acquir
        self.is_running = True
        self.IP = None
        self.SERVER_PORT = None
        self.data_count = 0

    def setConnectionParams(self, ip, port):
        """设置TCP连接参数"""
        self.IP = ip
        self.SERVER_PORT = port

    def run(self):
        """线程主函数 - 直接解析二进制数据"""
        if not self.IP or not self.SERVER_PORT:
            print("TCP连接参数未设置")
            return

        # 连接服务器
        while self.is_running:
            try:
                dataSocket = socket(AF_INET, SOCK_STREAM)
                dataSocket.settimeout(5)
                dataSocket.connect((self.IP, self.SERVER_PORT))
                print(f"成功连接到 {self.IP}:{self.SERVER_PORT}")
                break
            except Exception as e:
                print(f"连接失败: {e}, 5秒后重试...")
                time.sleep(5)
                continue

        print("开始接收数据...")

        # 数据接收循环
        while self.is_running:
            try:
                # 方案1: 先接收4字节长度头
                length_bytes = dataSocket.recv(4)
                if not length_bytes:
                    time.sleep(0.001)
                    continue

                # 解析数据长度（大端无符号整数）
                data_length = struct.unpack('>I', length_bytes)[0]

                # 接收实际数据
                data_buff = bytearray()
                while len(data_buff) < data_length:
                    packet = dataSocket.recv(min(data_length - len(data_buff), 65536))
                    if not packet:
                        break
                    data_buff.extend(packet)

                data_buff = bytes(data_buff)

                if len(data_buff) == data_length:
                    # 将数据转换为numpy数组
                    # 假设数据是大端字节序的双精度浮点数（>f8）
                    # 每个双精度浮点数占8字节
                    byte_count = len(data_buff)
                    if byte_count % 8 != 0:
                        print(f"警告: 数据长度不是8的倍数: {byte_count}")
                        continue

                    # 解析为双精度浮点数数组
                    data_array = np.frombuffer(data_buff, dtype='>f8')  # 大端双精度

                    # 调试：定期打印信息
                    self.data_count += 1
                    if self.data_count % 10 == 0:
                        print(f"已接收 {self.data_count} 个数据包, "
                              f"当前数据包: {len(data_array)} 个点")

                    # 发射信号
                    if len(data_array) > 0:
                        self.rawDataReady.emit(data_array)

                else:
                    print(f"数据接收不完整: {len(data_buff)}/{data_length} 字节")

            except Exception as e:
                print(f"数据接收错误: {e}")
                continue

        # 关闭socket
        try:
            dataSocket.close()
        except:
            pass

        print("数据接收线程结束")

    def stop(self):
        """停止线程"""
        self.is_running = False
        self.quit()
        self.wait()