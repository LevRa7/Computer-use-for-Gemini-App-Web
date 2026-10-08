#!/usr/bin/env node
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const isWindows = os.platform() === 'win32';
const argv = process.argv.slice(2);

// The two local maintenance subcommands run scripts that ship next to this CLI:
// in the installer payload, or in a repository checkout. Only the FIRST argument
// is inspected, so every other invocation - including the argument-passing
// bootstrap "-Quick", "-User <name>", "-Gateway <domain>" - is forwarded to the
// published installer exactly as before.
const LOCAL_SCRIPTS = {
  doctor: path.join('ops', 'doctor.ps1'),
  restart: path.join('ops', 'windows', 'agent-watchdog.ps1')
};

// Printed for "-h"/"-help"/"--help": the scripts declare their own parameter
// blocks, and forwarding a help flag to them only produced a PowerShell
// "parameter cannot be found" error.
const LOCAL_USAGE = {
  doctor: [
    'usage: gemini-computer-use doctor [-Json] [-ConfigDir <path>] [-TailLines <n>]',
    '  read-only node diagnostic: autostart interpreter, heartbeat, log tail, gateway state',
    '  exit code 0 when the node is healthy, 2 otherwise'
  ].join('\n'),
  restart: [
    'usage: gemini-computer-use restart [-Quiet] [-ConfigDir <path>] [-LogFile <path>] [-GraceSeconds <n>]',
    '  runs the watchdog once and starts the agent when the node is offline',
    '  exit code 0 when the node is online, 2 when the node is misconfigured, 3 when it is still offline'
  ].join('\n')
};

function findLocalScript(relative) {
  // The CLI is "bin/cli.js", so the payload/repository root is its parent; a
  // bundled layout that puts ops/ next to the CLI and the current directory are
  // accepted as well.
  const roots = [path.resolve(__dirname, '..'), path.resolve(__dirname), process.cwd()];
  for (const root of roots) {
    const candidate = path.join(root, relative);
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return null;
}

function runLocalScript(name, rest) {
  if (!isWindows) {
    console.error(`${name} is Windows-only: it drives Windows PowerShell 5.1 scripts.`);
    process.exit(1);
  }
  if (rest.some(arg => ['-h', '--h', '-help', '--help'].includes(arg.toLowerCase()))) {
    console.log(LOCAL_USAGE[name]);
    process.exit(0);
  }
  const relative = LOCAL_SCRIPTS[name];
  const script = findLocalScript(relative);
  if (!script) {
    console.error(`Cannot find ${relative.split(path.sep).join('/')} next to this CLI.`);
    console.error('Run this from an installed node or from a repository checkout: the');
    console.error('doctor and the watchdog ship with the installer payload.');
    process.exit(1);
  }
  // Windows PowerShell binds parameter names case-insensitively and accepts the
  // documented spelling, so a "--json" style flag only needs its second dash
  // removed to reach -Json. Everything else is passed through untouched.
  const forwarded = rest.map(arg => (arg.startsWith('--') ? arg.slice(1) : arg));
  const ps = spawn(
    'powershell.exe',
    ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script].concat(forwarded),
    { stdio: 'inherit' }
  );
  ps.on('error', (err) => {
    console.error(`${name}: could not start powershell.exe: ${err.message}`);
    process.exit(1);
  });
  ps.on('exit', (code) => process.exit(code === null ? 1 : code));
}

function launchRemoteInstaller(args) {
  const rawArgs = args.map(arg => `"${arg.replace(/"/g, '\\"')}"`).join(' ');

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
}

const subcommand = (argv[0] || '').toLowerCase();
if (Object.prototype.hasOwnProperty.call(LOCAL_SCRIPTS, subcommand)) {
  runLocalScript(subcommand, argv.slice(1));
} else {
  launchRemoteInstaller(argv);
}
