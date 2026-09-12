import http.server
import os
import sys
from http.server import ThreadingHTTPServer

class NoCacheHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # 禁用所有缓存，实现热部署效果
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def log_message(self, format, *args):
        # 让日志更简洁，兼容不同数量的参数
        safe_args = tuple(str(a) for a in args)
        try:
            msg = format % safe_args
        except TypeError:
            msg = ' '.join(safe_args)
        sys.stderr.write(f"[{self.log_date_time_string()}] {msg}\n")

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    directory = sys.argv[2] if len(sys.argv) > 2 else '.'

    os.chdir(directory)
    server = ThreadingHTTPServer(('0.0.0.0', port), NoCacheHTTPRequestHandler)
    print(f"热部署开发服务器已启动: http://0.0.0.0:{port}")
    print(f"服务目录: {os.path.abspath(directory)}")
    print("所有静态文件已禁用缓存，修改后刷新浏览器即可看到效果")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
        server.server_close()
