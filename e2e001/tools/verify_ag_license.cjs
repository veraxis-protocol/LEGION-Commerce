// Verifies the local AG Studio licence with AG Studio's own licence manager. Prints no key material.
const fs = require('fs');
const path = require('path');
const { AgStudioLicenseManager, AgStudioAiModule, VERSION } = require(path.join(__dirname, '../web/node_modules/ag-studio'));
const key = fs.readFileSync(process.argv[2], 'utf8').trim();
const out = {};
for (const [label, set] of [['core', new Set(['CORE'])], ['pro', new Set(['CORE','PRO'])], ['pro_ai', new Set(['CORE','PRO','PRO-AI'])]]) {
  try { const d = AgStudioLicenseManager.getLicenseDetails(key, set); delete d.licenseKey; out[label] = d; }
  catch (e) { out[label] = { error: String(e) }; }
}
out.ai_module_required_licence = AgStudioAiModule && (AgStudioAiModule.license || AgStudioAiModule.licence || null);
out.package_version = require(path.join(__dirname, '../web/node_modules/ag-studio/package.json')).version;
console.log(JSON.stringify(out, null, 1));
