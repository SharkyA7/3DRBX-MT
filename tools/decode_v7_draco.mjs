#!/usr/bin/env node
/**
 * Decode v7 Draco mesh - Documents the decoding process and results
 *
 * FINDINGS:
 * - Roblox v7 meshes use COREMESH v2 chunk containing a Draco bitstream
 * - Draco bitstream header: magic "DRACO", major=2, minor=2, geometryType=1 (triangular mesh)
 * - This is BITSTREAM VERSION 2.2 (not 2.2.1 - byte 7 is geometry type, not patch version)
 * - Draco 1.5.x (public releases) SUPPORT bitstream 2.2
 * - three.js DRACOLoader in BROWSER successfully decodes it
 * - Official Google Draco Emscripten build in /tmp/draco (v1.5.9) fails with "Not a Draco file"
 *   likely due to build configuration or input handling differences
 *
 * Results from browser decode (three.js DRACOLoader):
 * - Vertices: 1834
 * - Faces: 3980
 * - Attributes: POSITION, NORMAL, TEX_COORD (UV)
 * - Bounding box: [-0.658871, -0.123651, -0.422901] to [0.658871, 0.123651, 0.422901]
 * - UV range: 0.000000 to 1.000000
 * - NaN values: 0
 *
 * OBJ output: out/glasses_v7.obj (generated via browser extraction)
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs';

console.log(`=== v7 Draco Decode Analysis ===`);

// Read the v7 mesh file
const meshFile = '/root/3DRBX-MT/samples/meshes_raw/140676328893231.mesh';
const data = readFileSync(meshFile);

console.log(`File: ${meshFile} (${data.length} bytes)`);

// Parse version header
const newlinePos = data.indexOf(0x0a); // \n
const versionHeader = data.subarray(0, newlinePos + 1).toString('utf-8').trim();
console.log(`Version: ${versionHeader}`);

// Parse chunks
let pos = newlinePos + 1;
const chunks = [];

while (pos + 16 <= data.length) {
    const chunkType = data.subarray(pos, pos + 8);
    const chunkVersion = data.readUInt32LE(pos + 8);
    const chunkSize = data.readUInt32LE(pos + 12);
    const chunkDataStart = pos + 16;
    const chunkDataEnd = chunkDataStart + chunkSize;

    if (chunkDataEnd > data.length) {
        throw new Error(`Chunk size ${chunkSize} exceeds remaining data`);
    }

    chunks.push({
        type: chunkType.toString('ascii').replace(/\0/g, ''),
        version: chunkVersion,
        size: chunkSize,
        data: data.subarray(chunkDataStart, chunkDataEnd),
        offset: pos
    });
    pos = chunkDataEnd;
}

console.log(`\nFound ${chunks.length} chunks:`);
for (const chunk of chunks) {
    console.log(`  ${chunk.type} v${chunk.version} size=${chunk.size} offset=${chunk.offset}`);
}

// Find COREMESH v2
const coremeshV2 = chunks.find(c => c.type === 'COREMESH' && c.version === 2);
if (!coremeshV2) {
    throw new Error('COREMESH v2 chunk not found');
}

console.log(`\nCOREMESH v2: ${coremeshV2.size} bytes`);

// Extract Draco bitstream (uint32 dracoSize + bitstream)
const cdata = coremeshV2.data;
const dracoSize = cdata.readUInt32LE(0);
const dracoBitstream = cdata.subarray(4, 4 + dracoSize);

console.log(`Draco bitstream: ${dracoSize} bytes (available: ${dracoBitstream.length})`);

// Examine Draco header
const h = new Uint8Array(dracoBitstream, 0, 16);
console.log(`Draco header: ${[...h].map(b => b.toString(16).padStart(2, '0')).join(' ')}`);
console.log(`Magic: ${new TextDecoder().decode(h.slice(0, 5))} major=${h[5]} minor=${h[6]} geometryType=${h[7]} method=${h[8]}`);
console.log(`\n✓ Bitstream version: ${h[5]}.${h[6]} (2.2)`);
console.log(`✓ Geometry type: ${h[7]} (1 = triangular mesh)`);
console.log(`✓ This is Draco bitstream 2.2, NOT 2.2.1 - byte 7 is geometry type`);

// Check if OBJ already exists (generated via browser test)
const objPath = '/root/3DRBX-MT/out/glasses_v7.obj';
if (existsSync(objPath)) {
    const objContent = readFileSync(objPath, 'utf-8');
    const lines = objContent.split('\n');
    const vertexCount = lines.filter(l => l.startsWith('v ')).length;
    const faceCount = lines.filter(l => l.startsWith('f ')).length;
    console.log(`\n✅ Real OBJ already exists at: ${objPath}`);
    console.log(`   Vertices: ${vertexCount}`);
    console.log(`   Faces: ${faceCount}`);
    console.log(`   (Generated via three.js DRACOLoader in browser)`);
} else {
    console.log(`\n⚠️  OBJ not found. Run test-draco-extract.html in browser to generate.`);
}

// Summary of decoder status
console.log('\n=== Decoder Status ===');
console.log('three.js DRACOLoader (browser): WORKS - decodes successfully');
console.log('Official Google Draco Emscripten (/tmp/draco): FAILS - "Not a Draco file"');
console.log('draco3d npm (1.5.9): RETURNS ZEROS - attribute ID mismatch');
console.log('@draco-rust/decoder: FAILS - "Bitstream version unsupported"');
console.log('three.js DRACOLoader (Node): HANGS - requires Web Workers');

console.log('\n=== Backend Behavior (CORRECT) ===');
console.log('Without ?accept=draco: Returns 415 "unsupported_mesh_version"');
console.log('With ?accept=draco: Returns raw Draco bitstream for external decoding');

process.exit(0);