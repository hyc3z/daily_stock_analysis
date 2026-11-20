# 华为云账号与 SWR 操作教程

核验日期为 2026-11-20。面向中国大陆个人开发者，默认讲解 SWR 基础版。界面以用户控制台为准，企业版和国际站应核对相应文档。价格、免费额度和配额需在实际开通时查看，使用 SWR 不要求同时购买华为云服务器。构建机器和运行机器可来自其他云。

## 1. 注册账号

1. 打开华为云中国站 https://www.huaweicloud.com/ ，点击注册。已有华为账号的用户按页面提示登录并开通华为云。
2. 用户自行填写手机号、验证码和账号资料，设置密码。
3. 在账号中心完成适用的个人或企业实名认证，等待审核生效。账号未实名、受限或欠费时，SWR 创建与上传可能不可用。
4. 进入控制台搜索“容器镜像服务 SWR”，选择所需区域。首次授权弹窗按控制台说明处理。
5. 查看当前版本、计费说明和资源限制，再执行需要开通的操作。记录控制台的镜像仓库域名。

不要把云账号密码当作容器镜像登录密码。IAM 用户、AK/SK 和 SWR 登录用户名也各有用途。

官方入门提供了注册与实名认证入口。[SWR 入门](https://support.huaweicloud.com/qs-swr/) · [账号受限的影响](https://support.huaweicloud.com/productdesc-swr/swr_03_0007.html)

## 2. 建立镜像组织

进入 SWR → 组织管理 → 创建组织，填写自己的组织名称。这里的组织是镜像命名空间，与 GitHub org 及华为云 Organizations 服务分别管理。

以下示例统一使用这些参数。

| 参数 | 示例 |
| --- | --- |
| 区域项目 | cn-north-4，北京四 |
| 仓库域名 | swr.cn-north-4.myhuaweicloud.com |
| SWR 组织 | example-team |
| 镜像名 | demo-job |
| 集群 namespace | automation |
| 拉取 Secret | swr-pull |

镜像完整名称为 `仓库域名/组织/镜像名:tag`。仓库域名不包含 https://。默认使用私有镜像，并确认控制台实际可见性。

## 3. 创建自动化用户并授权

在 IAM → 用户 → 创建用户，分别建立两个用户，名字可以改。

| 用户示例 | 用途 | 目标权限 |
| --- | --- | --- |
| swr-ci-push | GitHub Actions 推送 | 指定组织或镜像的编辑权限 |
| swr-k3s-pull | K3s 下载 | 指定组织或镜像的读取权限 |

选择编程访问，并启用访问密钥方式。按页面生成、下载访问密钥文件，妥善保存。无需给自动化用户开启控制台登录，除非确实需要用该用户进入控制台。

创建用户时看到 admin 用户组，不必加入。先判断控制台采用的权限路径。

- **通用型登录**。管理员进入 SWR → 组织管理 → 目标组织 → 添加授权，给 CI 用户编辑权限，给拉取用户读取权限。也可对已有单个镜像授权。首次向组织创建新镜像时，需要具有相应组织权限。没有加入 IAM 用户组不能单独证明授权失败，应核对实际 SWR 授权及已有 IAM 策略。
- **增强型登录**。通过 IAM 给用户或其用户组配置适用区域和资源范围的策略，按官方当前授权项选择推送或拉取能力。SWR 组织页面的本地授权不能替代这条权限路径。生成策略前核实当前 IAM 平面、资源粒度和依赖授权项，避免凭印象编造策略 JSON。

若用户已有 SWR Admin 或 Tenant Administrator 等广泛权限，仅在组织页面改为读取无法消除继承的管理权限。

[镜像授权管理](https://support.huaweicloud.com/usermanual-swr/swr_01_0015.html) · [权限管理](https://support.huaweicloud.com/productdesc-swr/swr_03_0005.html)

## 4. 取得 SWR 登录凭据

进入 SWR 的“登录指令”入口。

| 方式 | 使用场景和注意事项 |
| --- | --- |
| 通用型临时登录 | 临时操作。以页面过期时间为准，过期后重新生成 |
| 通用型长期登录 | 自动化流程。以对应 IAM 用户的 AK/SK 生成，不使用主账号密钥代替 |
| 增强型登录 | IAM 新平面场景，仅临时凭据，需要按当前有效期设计刷新流程 |

通用型长期登录页面通常要求导入 credentials.csv 或填写 AK 和 SK。生成的是 SWR 专用登录用户名和密码。用户名示例为 `cn-north-4@AK标识`，必须使用完整值。密码采用页面生成值，不能直接填 SK。

CI 用户和拉取用户分别生成各自凭据。用主账号生成的登录指令不会自动变成新 IAM 用户的凭据。登录区域与目标镜像仓库必须一致。

长期凭据不需要每次交互登录，但本地凭据清理、新 runner、新节点或 Secret 缺失会要求重新提供。AK 被禁用、删除或权限撤销后，应按已失效处理并验证。官方部分页面将长期凭据称为永久有效，实际运维仍要跟踪底层访问密钥状态。增强型临时凭据需在到期前自动更新，不能靠只配置一次 Secret 长期运行。

[生成登录凭据](https://support.huaweicloud.com/usermanual-swr/swr_01_1000.html) · [凭据有效期 FAQ](https://support.huaweicloud.com/swr_faq/swr_faq_0015.html)

## 5. 用 Docker 验证推送与下载

以下为 Bash，适用于 Linux、WSL 或 Git Bash，需要已安装并可运行 Docker。Windows PowerShell 应改为对应语法，不能原样粘贴 read 命令。使用自己的 Dockerfile，构建失败时先检查基础镜像是否可下载。

```bash
SWR_REGISTRY='swr.cn-north-4.myhuaweicloud.com'
SWR_ORG='example-team'
SWR_IMAGE='demo-job'
SWR_TAG="$(git rev-parse HEAD)"
read -r -p 'CI 用户的完整 SWR 用户名: ' SWR_USERNAME
read -r -s -p 'CI 用户的 SWR 登录密码: ' SWR_PASSWORD
printf '\n'
printf '%s' "$SWR_PASSWORD" | docker login "$SWR_REGISTRY" \
  --username "$SWR_USERNAME" --password-stdin
unset SWR_PASSWORD

docker build -t "$SWR_REGISTRY/$SWR_ORG/$SWR_IMAGE:$SWR_TAG" .
docker push "$SWR_REGISTRY/$SWR_ORG/$SWR_IMAGE:$SWR_TAG"
```

构建架构应匹配运行节点，例如 x86_64 对应 linux/amd64。成功推送后记录 digest，在 SWR 控制台核对版本。然后用拉取用户的凭据，在目标下载环境验证同一完整镜像名称。不要用 CI 用户的成功结果代替读取用户验证。

## 6. 接入 GitHub Actions

仓库 Settings → Secrets and variables → Actions → Repository secrets，保存 CI 用户的两个值。

- `SWR_USERNAME` 保存完整 SWR 登录用户名。
- `SWR_PASSWORD` 保存生成的 SWR 登录密码。

仓库域名、组织、镜像名可用普通 Variables 管理。以下示例假定根目录已有 Dockerfile，手动运行后发布一个 commit SHA 标签。新建文件位置为 `.github/workflows/publish-swr.yaml`。

```yaml
name: Publish to SWR
on:
  workflow_dispatch:
permissions:
  contents: read
jobs:
  publish:
    runs-on: ubuntu-latest
    env:
      SWR_REGISTRY: swr.cn-north-4.myhuaweicloud.com
      SWR_ORG: example-team
      SWR_IMAGE: demo-job
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ${{ env.SWR_REGISTRY }}
          username: ${{ secrets.SWR_USERNAME }}
          password: ${{ secrets.SWR_PASSWORD }}
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          platforms: linux/amd64
          tags: ${{ env.SWR_REGISTRY }}/${{ env.SWR_ORG }}/${{ env.SWR_IMAGE }}:${{ github.sha }}
```

示例 action 版本沿用本项目已使用的主版本，新接入时核实其当前支持情况。需要自动触发时再按用户分支规则添加 push 事件。

若实际遇到 SWR manifest/media type 不兼容，检查 registry 响应和构建日志。本项目曾验证的兼容导出为 `outputs: type=image,push=true,oci-mediatypes=false`，同时使用 `provenance: false` 和 `sbom: false`。使用这一输出方式时替换示例中的 `push: true`，并明确关闭了相应构建元数据。不要对所有 SWR 仓库预设该兼容需求。

CI 发布镜像完成后，部署清单仍需更新镜像 tag 或 digest。Argo CD 同步 Git 中的清单，其 Git 仓库凭据与 SWR 拉取凭据分别配置。

[GitHub 官方镜像发布指南](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)

## 7. 接入 K3s 或 Kubernetes

在目标 namespace 创建 kubernetes.io/dockerconfigjson 类型的 Secret，填写拉取用户凭据。Secret 与 Pod 必须在同一 namespace。

以下在管理员本地终端执行。要求 Python 3、kubectl 及正确 kubeconfig，先用 `kubectl config current-context` 核对集群。仅有 K3s 自带命令时，可将代码中的 `['kubectl']` 改为 `['sudo', '-n', 'k3s', 'kubectl']`。

保存以下代码为临时 Python 文件并通过 `python3 文件名.py` 运行。凭据通过交互输入，在内存组装后经标准输入提交，只输出 Secret 创建结果。

```python
import base64
import getpass
import json
import subprocess

registry = input("SWR 仓库域名（不含 https://）: ").strip()
namespace = input("目标 namespace: ").strip()
username = input("拉取用户完整 SWR 用户名: ").strip()
password = getpass.getpass("拉取用户 SWR 登录密码: ")
if not all([registry, namespace, username, password]):
    raise SystemExit("必填项不能为空")
if "://" in registry or "/" in registry or any(c.isspace() for c in registry):
    raise SystemExit("仓库域名格式错误")
auth = base64.b64encode(f"{username}:{password}".encode()).decode()
config = {"auths": {registry: {"auth": auth}}}
secret = {
    "apiVersion": "v1",
    "kind": "Secret",
    "metadata": {"name": "swr-pull", "namespace": namespace},
    "type": "kubernetes.io/dockerconfigjson",
    "data": {
        ".dockerconfigjson": base64.b64encode(json.dumps(config).encode()).decode()
    },
}
kubectl = ["kubectl"]
subprocess.run(kubectl + ["get", "namespace", namespace], check=True,
               stdout=subprocess.DEVNULL)
subprocess.run(kubectl + ["apply", "--server-side",
                         "--field-manager=swr-setup", "-f", "-"],
               input=json.dumps(secret), text=True, check=True)
```

namespace 应已存在。服务器端字段冲突应先核对现有 Secret 的管理者，不直接强制覆盖。Kubernetes Secret 的 Base64 编码可还原，不把生成对象打印到聊天或提交 Git。

把以下字段写入实际工作负载的 PodSpec。Deployment 位于 `spec.template.spec`；CronJob 位于 `spec.jobTemplate.spec.template.spec`。

```yaml
imagePullSecrets:
  - name: swr-pull
containers:
  - name: worker
    image: swr.cn-north-4.myhuaweicloud.com/example-team/demo-job:COMMIT_SHA
```

替换 COMMIT_SHA，再按项目部署方式应用清单。验证时读 Pod 的 Events、容器状态和实际日志。可经用户授权创建独立下载测试 Pod，使用同一个 Secret，令 imagePullPolicy 为 Always，再核对启动结果。这个测试仍会下载镜像并启动容器。

```bash
kubectl get pods -n automation
kubectl describe pod POD_NAME -n automation
kubectl get events -n automation --field-selector type=Warning
```

Docker 客户端登录不会自动给 K3s/containerd 配置凭据。直接 crictl/ctr 拉取也不会自动读取 Kubernetes 的 imagePullSecrets，验证 Pod 的拉取流程更贴近实际部署。更新 CronJob 模板不会改变已经创建的 Job。

[Kubernetes 私有镜像下载](https://kubernetes.io/docs/tasks/configure-pod-container/pull-image-private-registry/)

## 8. 故障定位

| 现象 | 先核对的证据与处理 |
| --- | --- |
| SWR 首次授权失败 | 实名认证、账号受限或欠费、区域及当前开通页面 |
| 登录成功但 push 被拒绝 | 凭据所属 IAM 用户、目标组织、编辑授权、通用/增强路径 |
| 401 或 unauthorized | 凭据是否过期、用户名是否含区域前缀、域名是否匹配 |
| 403、denied 或 insufficient_scope | 组织/镜像/tag 拼写、目标用户有效权限；不能仅凭错误词判定一定是密码 |
| 长时间 Pulling | 读取 Pod Events、超时错误、节点 DNS/TLS、实际镜像层下载；根路径 HTTP 200/401/405 不能证明层可下载 |
| ImagePullBackOff | 读取此前 Failed 事件及具体 registry 错误，这是重试退避状态 |
| manifest unknown | CI 是否已发布该 SHA、路径及 tag 是否存在 |
| no matching manifest | 镜像平台与节点架构是否一致 |
| 登录凭据过几小时失效 | 检查是否把临时或增强型凭据用于长期任务，配置刷新或选择适用的长期凭据 |
| Docker Hub 权限异常 | 先检查现有 K3s registries.yaml 的 mirror。普通阿里云命名空间仓库不能直接充当所有 docker.io 镜像的透明镜像源 |
| OCI/manifest 不兼容 | 核对 SWR 版本与实际导出格式，需要时采用上面的兼容导出 |
| Argo CD Git 连接成功但 Pod 下载失败 | 分别检查 Git 凭据、镜像地址和 namespace 内拉取 Secret |
| 没有 Dockerfile | 先完成容器化；只有源代码仓库不足以直接 push 成为镜像 |

SWR 保存自己已推送的镜像。基础镜像的下载和构建依赖仍要独立满足。运行机器不必与 SWR 同一家云或同一区域，但应检查可达性、跨地域流量与相应费用。

## 教学与操作交付

按用户当前阶段给出下一步，记录已选区域、组织和镜像完整名称，不记录凭据。逐项标注实际验收结果，包括账号可用、授权、推送版本、拉取身份与集群下载。没有执行实际云操作时，明确说明教程和示例已准备、哪些运行步骤仍需配置。
