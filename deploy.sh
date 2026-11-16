#!/bin/bash
# YYB Go Enhanced 一键部署 —— 装在青龙同一台机器的子目录里
# 用法：
#   QL_CLIENT_ID=xxx QL_CLIENT_SECRET=xxx bash deploy.sh [安装目录]
#   安装目录默认：./yyb-go（当前目录下的 yyb-go 子目录）
#   也可以只传目录，脚本会提示输入 Client ID / Secret：
#   bash deploy.sh /root/qinglong/yyb-go
# 要求：本机已装 Docker + Docker Compose v2，青龙容器名默认为 qinglong
set -e

INSTALL_DIR="${1:-./yyb-go}"
QL_CONTAINER="${QL_CONTAINER:-qinglong}"
NETWORK="${YYB_DOCKER_NETWORK:-qinglong_default}"

command -v docker >/dev/null 2>&1 || { echo "没找到 docker，先装 Docker 再跑这个脚本"; exit 1; }
if docker compose version >/dev/null 2>&1; then COMPOSE="docker compose"; elif command -v docker-compose >/dev/null 2>&1; then COMPOSE="docker-compose"; else echo "没找到 Docker Compose v2"; exit 1; fi

if [ -z "$QL_CLIENT_ID" ]; then read -p "青龙应用 Client ID: " QL_CLIENT_ID; fi
if [ -z "$QL_CLIENT_SECRET" ]; then read -s -p "青龙应用 Client Secret: " QL_CLIENT_SECRET; echo; fi
[ -n "$QL_CLIENT_ID" ] && [ -n "$QL_CLIENT_SECRET" ] || { echo "Client ID / Secret 不能为空"; exit 1; }

mkdir -p "$INSTALL_DIR/data/db" "$INSTALL_DIR/data/avatars" "$INSTALL_DIR/data/qr"
cd "$INSTALL_DIR"

# 官方 compose.yaml（原样下载，依次试直连、jsDelivr、gh-proxy）
for COMPOSE_URL in \
  "https://raw.githubusercontent.com/525815266/YYB-Go-Enhanced/main/compose.yaml" \
  "https://cdn.jsdelivr.net/gh/525815266/YYB-Go-Enhanced@main/compose.yaml" \
  "https://gh-proxy.org/https://raw.githubusercontent.com/525815266/YYB-Go-Enhanced/main/compose.yaml"; do
  curl -fsSL --max-time 30 "$COMPOSE_URL" -o compose.yaml && break
  echo "下载失败，换下一个源：$COMPOSE_URL"
done
[ -s compose.yaml ] || { echo "compose.yaml 三个源都下不到，先修网络再跑"; exit 1; }

# 生成 .env：同机用容器名直连青龙，协议令牌随机生成
PROTOCOL_TOKEN="$(openssl rand -hex 16 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
cat > .env <<EOF
YYB_IMAGE=ghcr.io/525815266/yyb-go-enhanced:latest
YYB_DOCKER_NETWORK=$NETWORK
YYB_AUTH_DRIVER=sqlite
YYB_AUTH_DSN=
YYB_ADMIN_USER=
YYB_ADMIN_PASSWORD=
YYB_ACCOUNT_LINK_KEY=
YYB_COOKIE_SECURE=false
YYB_PROTOCOL_TOKEN=$PROTOCOL_TOKEN
YYB_INTEGRATION_TOKEN=
YYB_QINGLONG_REF_MODE=id
YYB_ENABLE_PC_LOGIN=false
YYB_KEEPALIVE_INTERVAL=2m
YYB_KEEPALIVE_AHEAD=4m
YYB_DEVICE_IDENTITY=stable
YYB_NOTIFY_CHANNEL=
YYB_NOTIFY_TOKEN=
YYB_CIRCUIT_BREAKER=on
YYB_PORT=8000
YYB_BIND_ADDRESS=0.0.0.0
PANEL_TYPE=qinglong
QL_URL=http://qinglong:5700
QL_CLIENT_ID=$QL_CLIENT_ID
QL_CLIENT_SECRET=$QL_CLIENT_SECRET
YYB_QINGLONG_SERVER=yyb-go:8000
YYB_QINGLONG_REPO=SuperNaiBA_YYB-GO-Script,525815266_YYB-Go-Enhanced/scripts
EOF

# 共享网络：没有就建一个，青龙容器没连上就连上
docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK"
if docker inspect "$QL_CONTAINER" >/dev/null 2>&1; then
  docker inspect "$QL_CONTAINER" --format '{{json .NetworkSettings.Networks}}' | grep -q "$NETWORK" \
    || docker network connect "$NETWORK" "$QL_CONTAINER"
fi

# 让容器里的 yyb 用户能写 data 目录
docker run --rm --user 0 --entrypoint sh \
  -v "$PWD/data:/data" ghcr.io/525815266/yyb-go-enhanced:latest \
  -c 'chown -R yyb:yyb /data/db /data/avatars /data/qr && chmod -R u+rwX /data/db /data/avatars /data/qr'

$COMPOSE pull yyb-go
$COMPOSE up -d --no-build

sleep 5
echo "---- 健康检查 ----"
curl -fsS http://127.0.0.1:8000/health && echo || echo "health 未通，看一下 docker logs yyb-go"
echo "装好了，浏览器打开 http://<这台机器的IP>:8000，注册第一个用户就是管理员，然后扫码加微信。"
