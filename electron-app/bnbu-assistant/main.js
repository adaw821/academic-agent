const { app, BrowserWindow } = require('electron');
const path = require('path');

function createWindow() {
    const win = new BrowserWindow({
        width: 1200,
        height: 800,
        icon: path.join(__dirname, 'assets', 'bnbu-icon.ico'),
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true
        }
    });
    
    // 调试：打开开发者工具
    win.webContents.openDevTools();
    
    // 监听页面错误
    win.webContents.on('did-fail-load', (event, code, desc, url) => {
        console.error('Page load failed:', code, desc, url);
    });
    
    win.loadFile('index.html');
}

app.whenReady().then(createWindow);
