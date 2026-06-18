/**
 * BNBU 启动器 - 解决 ELECTRON_RUN_AS_NODE 环境变量冲突
 * 用法: node launcher.js
 */
const { spawn, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

// 强制清除冲突的环境变量
delete process.env.ELECTRON_RUN_AS_NODE;
delete process.env.NODE_OPTIONS;
delete process.env.ACC_PRODUCT_CONFIG_V3;
delete process.env.ACC_PRODUCT_CONFIG_PATH;
delete process.env.ACC_PRODUCT_CONFIG;

// 确保子进程也不继承这些变量
const cleanEnv = { ...process.env };
delete cleanEnv.ELECTRON_RUN_AS_NODE;
delete cleanEnv.NODE_OPTIONS;

/**
 * 智能查找 electron 可执行文件
 * 优先返回 .exe 文件（Windows 上 spawn .exe 不需要 shell:true）
 */
function findElectron() {
    const dir = path.join(__dirname, 'node_modules');

    // === 策略 1: electron/dist/electron.exe（最可靠，真正的 exe） ===
    const localExe = path.join(dir, 'electron', 'dist', 'electron.exe');
    if (fs.existsSync(localExe)) {
        console.log('[Launcher] Found Electron at:', localExe);
        return { path: localExe, useShell: false };
    }

    // === 策略 2: 从 .bin/electron.cmd 反查 exe 路径 ===
    const binCmd = path.join(dir, '.bin', 'electron.cmd');
    if (fs.existsSync(binCmd)) {
        // .cmd 文件里通常写着真正的 electron 路径
        try {
            const cmdContent = fs.readFileSync(binCmd, 'utf8');
            // electron.cmd 里通常有类似 "%~dp0\\..\electron\dist\electron.exe" %*
            const match = cmdContent.match(/["']?([^"'\s]*electron\.exe)["']?/i);
            if (match) {
                // 相对于 .cmd 所在目录解析
                const resolved = path.resolve(path.dirname(binCmd), match[1]);
                if (fs.existsSync(resolved)) {
                    console.log('[Launcher] Found Electron (from .cmd):', resolved);
                    return { path: resolved, useShell: false };
                }
            }
        } catch (e) {}
        // 反查失败但 .cmd 存在 → 用 shell 模式运行 .cmd
        console.log('[Launcher] Using electron.cmd with shell mode');
        return { path: binCmd, useShell: true };
    }

    // === 策略 3: 全局安装的 electron ===
    try {
        const globalRoot = execSync('npm root -g', { encoding: 'utf8' }).trim();
        const globalExe = path.join(globalRoot, 'electron', 'dist', 'electron.exe');
        if (fs.existsSync(globalExe)) {
            console.log('[Launcher] Found global Electron at:', globalExe);
            return { path: globalExe, useShell: false };
        }
    } catch (e) {}

    // === 策略 4: electron/path.txt ===
    try {
        const pathTxt = path.join(dir, 'electron', 'path.txt');
        if (fs.existsSync(pathTxt)) {
            const exePath = fs.readFileSync(pathTxt, 'utf8').trim();
            if (fs.existsSync(exePath)) {
                console.log('[Launcher] Found Electron (path.txt):', exePath);
                return { path: exePath, useShell: false };
            }
        }
    } catch (e) {}

    return null;
}

const result = findElectron();

if (!result) {
    console.error('\n❌ 找不到 Electron！请先运行：\n');
    console.error('   cd electron-app\\bnbu-assistant');
    console.error('   npm install\n');
    process.exit(1);
}

// 启动 Electron
const child = spawn(result.path, ['main.js'], {
    stdio: 'inherit',
    env: cleanEnv,
    windowsHide: false,
    cwd: __dirname,
    shell: result.useShell   // .cmd 文件必须设为 true
});

child.on('close', (code) => {
    process.exit(code || 0);
});
