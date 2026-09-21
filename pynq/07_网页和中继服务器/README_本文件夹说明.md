# 07 网页和中继服务器

这个文件夹负责网页控制台和云端 relay 服务。

- test_web_client.html：当前网页控制台 HTML。支持登录/注册、Token 兼容连接、视频显示、遥控按键、速度滑块、急停、循迹、语音面板、校园跑记录和管理员面板。
- quick_relay_server.py：中继服务器主程序。负责网页端和车端 WebSocket 转发、Token 校验、账号会话、驾驶权限、控制权锁、急停优先级、运行记录和安全日志。
- SERVER_DEPLOY_部署说明.md：服务器部署说明参考。

中继地址、数据库路径和鉴权 token 通过环境变量配置；部署方法见 `SERVER_DEPLOY_部署说明.md`。
