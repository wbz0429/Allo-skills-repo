---
name: publish-dfcode
description: 发布 dfcode 到 npm。支持正式版和 beta 版。会自动拉取 feature/dev 最新代码、构建所有平台二进制、打包并发布到 npm。
---

## 环境要求

执行此 skill 前需确保以下条件已满足：

### 1. 克隆 dfcode 源码

```bash
git clone git@github.com:wbz0429/dfcode.git
cd dfcode
```

后续所有命令在 `packages/dfcode` 子目录下执行。

### 2. Git 权限

需要有 dfcode 仓库的读写权限（当前 owner: wbz0429）。如果是团队其他成员，需要先被添加为仓库 collaborator。

### 3. npm 发布权限

需要有 npm 包 `dfcode` 的 publish 权限。**使用 wbz0429 的 npm token：**

```bash
npm config set //registry.npmjs.org/:_authToken <wbz0429的npm_token>
```

或临时设置环境变量：

```bash
export NPM_TOKEN=<wbz0429的npm_token>
```

> npm 账号 wbz0429 拥有 `dfcode`、`dfcode-darwin-arm64`、`dfcode-darwin-x64`、`dfcode-linux-x64`、`dfcode-linux-arm64`、`dfcode-windows-x64` 六个包的发布权限。

### 4. 工具依赖

- **bun** — 依赖安装和构建
- **npm** — 打包（`npm pack`）和发布（`npm publish`）

确认已安装：

```bash
bun --version
npm --version
```

## 使用方式

- `/publish-dfcode` — 发布正式版（latest tag）
- `/publish-dfcode beta` — 发布 beta 版（beta tag）

## 执行步骤

### 0. 前置检查（必须）

**检查 package.json 版本号是否已更新：**

```bash
cd packages/dfcode
cat package.json | grep version
```

**关键：** 构建时二进制会读取 `packages/dfcode/package.json` 中的 `version` 字段并编译进去。如果版本号不对，即使后面打包时指定了正确版本，`dfcode --version` 输出的仍然是旧版本。

**必须先更新 package.json 版本号再构建！**

### 1. 确认版本号

先查看当前已发布的最新版本：

```bash
npm view dfcode dist-tags --json
```

根据用户指定的版本号，或者在当前 latest 版本基础上递增版本（如 1.0.16 → 2.0.0 major，或 1.0.16 → 1.0.17 patch）。如果是 beta，格式为 `x.x.x-beta.N`。

**在继续之前，告知用户将要发布的版本号，并等待确认。**

### 2. 更新版本号（关键步骤）

**必须在构建前更新 `packages/dfcode/package.json` 中的版本号：**

```bash
cd packages/dfcode
# 手动编辑或用工具更新
# 例如：将 "version": "1.0.16" 改为 "version": "2.0.0"
```

**验证版本号已更新：**

```bash
cat package.json | grep '"version"'
```

### 3. 拉取最新代码

```bash
git pull origin feature/dev
```

如果失败（网络问题），提示用户检查代理后重试，不要继续后续步骤。

### 4. 安装依赖

```bash
bun install
```

### 5. 构建所有平台

在 `packages/dfcode` 目录下执行：

```bash
bun run script/build.ts
```

构建产物在 `packages/dfcode/dist/` 下，包含：
- `dfcode-darwin-arm64/`
- `dfcode-darwin-x64/`
- `dfcode-linux-x64/`
- `dfcode-linux-arm64/`
- `dfcode-windows-x64/`

**验证构建的二进制版本号（重要）：**

```bash
./dist/dfcode-darwin-arm64/bin/dfcode --version
```

确保输出的版本号与目标版本一致。如果不一致，说明步骤 2 没有正确更新 package.json，必须回到步骤 2 重新操作。

### 6. 打包平台二进制包

对每个平台包创建 `package.json` 并打包（在 `packages/dfcode` 目录下执行）：

```bash
VERSION="2.0.0"  # 替换为实际版本号

for name in dfcode-darwin-arm64 dfcode-darwin-x64 dfcode-linux-x64 dfcode-linux-arm64 dfcode-windows-x64; do
  os=$(echo $name | sed 's/dfcode-//' | cut -d'-' -f1 | sed 's/windows/win32/')
  arch=$(echo $name | sed 's/dfcode-//' | cut -d'-' -f2)
  cat > "./dist/$name/package.json" << EOF
{
  "name": "$name",
  "version": "$VERSION",
  "os": ["$os"],
  "cpu": ["$arch"]
}
EOF
  echo "Created package.json for $name"
done
```

**打包（使用 npm pack，不要用 bun pm pack）：**

```bash
cd dist/dfcode-darwin-arm64 && npm pack && cd ../..
cd dist/dfcode-darwin-x64 && npm pack && cd ../..
cd dist/dfcode-linux-x64 && npm pack && cd ../..
cd dist/dfcode-linux-arm64 && npm pack && cd ../..
cd dist/dfcode-windows-x64 && npm pack && cd ../..
```

或者用绝对路径避免 cd 问题：

```bash
cd /path/to/packages/dfcode/dist/dfcode-darwin-arm64 && npm pack
cd /path/to/packages/dfcode/dist/dfcode-darwin-x64 && npm pack
cd /path/to/packages/dfcode/dist/dfcode-linux-x64 && npm pack
cd /path/to/packages/dfcode/dist/dfcode-linux-arm64 && npm pack
cd /path/to/packages/dfcode/dist/dfcode-windows-x64 && npm pack
```

### 7. 发布平台包

**逐个**发布，不要并行（网络不稳定时并行容易失败）：

```bash
VERSION="2.0.0"  # 替换为实际版本号
TAG_FLAG=""      # 正式版留空，beta 版设为 "--tag beta"

cd dist/dfcode-darwin-arm64 && npm publish dfcode-darwin-arm64-$VERSION.tgz --access public $TAG_FLAG && cd ../..
cd dist/dfcode-darwin-x64 && npm publish dfcode-darwin-x64-$VERSION.tgz --access public $TAG_FLAG && cd ../..
cd dist/dfcode-linux-x64 && npm publish dfcode-linux-x64-$VERSION.tgz --access public $TAG_FLAG && cd ../..
cd dist/dfcode-linux-arm64 && npm publish dfcode-linux-arm64-$VERSION.tgz --access public $TAG_FLAG && cd ../..
cd dist/dfcode-windows-x64 && npm publish dfcode-windows-x64-$VERSION.tgz --access public $TAG_FLAG && cd ../..
```

- 正式版不加 `--tag`（默认 latest）
- beta 版加 `--tag beta`
- 每个包 timeout 设置 600 秒
- 如果失败直接重试，不要并行

### 8. 打包主包

准备 `dist/dfcode/` 目录：

```bash
cd packages/dfcode
rm -rf dist/dfcode/bin && mkdir -p dist/dfcode/bin
cp ./bin/dfcode dist/dfcode/bin/dfcode
chmod 755 dist/dfcode/bin/dfcode
cp ./script/postinstall.mjs dist/dfcode/postinstall.mjs
```

创建 `dist/dfcode/package.json`：

```bash
VERSION="2.0.0"  # 替换为实际版本号

cat > dist/dfcode/package.json << EOF
{
  "name": "dfcode",
  "version": "$VERSION",
  "description": "AI-powered coding assistant - TUI interface",
  "bin": {
    "dfcode": "./bin/dfcode"
  },
  "scripts": {
    "postinstall": "node ./postinstall.mjs"
  },
  "optionalDependencies": {
    "dfcode-darwin-arm64": "$VERSION",
    "dfcode-darwin-x64": "$VERSION",
    "dfcode-linux-x64": "$VERSION",
    "dfcode-linux-arm64": "$VERSION",
    "dfcode-windows-x64": "$VERSION"
  },
  "license": "MIT",
  "keywords": ["ai", "coding", "assistant", "cli", "tui", "llm", "claude", "openai", "gpt"]
}
EOF
```

打包：

```bash
cd dist/dfcode && npm pack
```

### 9. 发布主包

```bash
VERSION="2.0.0"  # 替换为实际版本号
TAG_FLAG=""      # 正式版留空，beta 版设为 "--tag beta"

cd dist/dfcode && npm publish dfcode-$VERSION.tgz --access public $TAG_FLAG
```

### 10. 验证

**正式版必须同步 stable tag（重要）：**

正式版发布后，`latest` 会自动指向新版本，但 `stable` 不会自动更新。`dfcode upgrade` 对 stable channel 安装包会读取 npm 的 `stable` tag；如果 `stable` 仍指向旧版本，用户运行 `dfcode upgrade` 会提示旧版本已安装并跳过升级。

```bash
VERSION="2.0.0"  # 替换为实际版本号
npm dist-tag add dfcode@$VERSION stable
```

beta 版不要更新 `stable`。

```bash
npm install -g dfcode@$VERSION
dfcode --version
```

**验证输出的版本号是否正确。** 如果版本号不对，说明步骤 2 没有正确更新 package.json。

**验证 npm dist-tags：**

```bash
npm view dfcode dist-tags --json
npm view dfcode@stable version --json
```

确认正式版的 `latest` 和 `stable` 都指向新版本（或 beta 版的 `beta` 指向新 beta 版本）。

### 11. 测试各平台（可选但推荐）

如果有条件，在不同平台上测试安装：

```bash
# macOS (arm64)
npm install -g dfcode@$VERSION
dfcode --version

# macOS (x64)
npm install -g dfcode@$VERSION
dfcode --version

# Linux (x64)
npm install -g dfcode@$VERSION
dfcode --version

# Windows (x64)
npm install -g dfcode@$VERSION
dfcode --version
```

## 常见问题

### Q1: 发布后 `dfcode --version` 显示旧版本号

**原因：** 构建前没有更新 `packages/dfcode/package.json` 中的版本号，导致二进制编译时内嵌了旧版本。

**解决：**
1. 更新 `packages/dfcode/package.json` 版本号
2. 重新构建（步骤 5）
3. 用新版本号重新打包和发布（步骤 6-9）
4. npm 不允许重复发布同一版本，需要递增版本号（如 2.0.0 → 2.0.1）

### Q2: npm publish 报错 "cannot publish over existing version"

**原因：** 该版本号已经发布过，npm 不允许覆盖。

**解决：** 递增版本号（如 2.0.0 → 2.0.1），重新执行步骤 2-9。

### Q3: postinstall 失败 "Could not find package dfcode-xxx"

**原因：** npm 安装时 optionalDependencies 还没下载完就执行了 postinstall。

**解决：** 重新安装一次通常就能成功：

```bash
npm install -g dfcode@$VERSION
```

### Q4: 打包时 `bun pm pack` 没有生成 .tgz 文件

**原因：** `bun pm pack` 在某些情况下不生成 tarball。

**解决：** 使用 `npm pack` 代替：

```bash
cd dist/dfcode-darwin-arm64 && npm pack
```

## 注意事项

- **最关键：** 构建前必须先更新 `packages/dfcode/package.json` 版本号
- 所有命令在 `packages/dfcode` 目录下执行（除非使用绝对路径）
- 网络不稳定时 npm publish 会超时，直接重试即可，不要并行发布
- 使用 `npm pack` 而不是 `bun pm pack`
- Windows 二进制用 `shell: true` 执行（已在 bin/dfcode 中修复）
- `bin/dfcode` 和 `postinstall.mjs` 中包名必须是 `dfcode-*`，不是 `opencode-*`
- beta 版主包版本号格式：`x.x.x-beta.N`，平台包可以复用已有版本
- 发布前务必验证构建的二进制版本号（步骤 5 末尾）
- 发布后务必验证安装的版本号（步骤 10）
- 正式版发布后必须将 `stable` tag 同步到新版本，否则 stable channel 的 `dfcode upgrade` 会继续指向旧版本

## 回滚

如果发布后发现严重问题，可以将 `latest` tag 指向上一个稳定版本：

```bash
# 查看历史版本
npm view dfcode versions --json

# 将 latest 指向旧版本
npm dist-tag add dfcode@1.0.16 latest

# 将有问题的版本标记为 deprecated
npm deprecate dfcode@2.0.0 "This version has critical bugs, please use 2.0.1 or 1.0.16"
```
