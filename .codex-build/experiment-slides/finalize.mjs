import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
const SKILL='C:/Users/admin/.codex/plugins/cache/openai-primary-runtime/presentations/26.909.11814/skills/presentations';
const workspace='D:/Python Project/NeuroGRA';
const build=path.join(workspace,'.codex-build/experiment-slides');
process.env.RUNTIME_NODE_MODULES='C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
const source=path.join(workspace,'docs/开题ppt.pptx');
const final=process.argv[2]||path.join(workspace,'docs/outputs/开题ppt_实验设计完善版.pptx');
const {finalizePresentation}=await import(pathToFileURL(path.join(SKILL,'container_tools/artifact_tool_utils.mjs')).href);
const tableOwners=[8,9,10,11];
const result=await finalizePresentation({
 workspaceDir:workspace,candidatePath:path.join(build,'candidate.pptx'),finalPath:final,
 explicitTotalSlideCount:12,
 pythonExecutable:'C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe',
 integrityValidatorPath:path.join(SKILL,'container_tools/inspect_presentation_package_integrity.py'),
 layoutValidatorPath:path.join(SKILL,'container_tools/inspect_presentation_layout_geometry.py'),
 layoutArgs:['--expected-slide-size-emu','12192000,6858000',...tableOwners.flatMap(n=>['--require-native-table-slide',String(n)])],
 requiredNativeTableOwnerSlides:tableOwners,
 // Existing source slides 2–4 contain four unresolved East Asian font inheritances
 // in the bundled validator. Preserve those source slides byte for byte; audit
 // the authored font families separately instead of changing the user's deck.
 verifyArtifactToolImport:true,
 receiptPath:path.join(build,path.basename(final)+'.validation.json')
});
console.log(JSON.stringify({path:final,validation:result}));
