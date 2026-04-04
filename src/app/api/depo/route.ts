import { NextRequest, NextResponse } from 'next/server';
import { getIronSession } from 'iron-session';
import { cookies } from 'next/headers';
import { sessionOptions, SessionData } from '@/lib/session';
import { getFreshToken, uploadToBox } from '@/lib/box';
import { createJob, updateJob, appendLog } from '@/lib/jobs';
import { spawn } from 'child_process';
import { promises as fs } from 'fs';
import os from 'os';
import path from 'path';
import crypto from 'crypto';

export async function POST(request: NextRequest) {
  const session = await getIronSession<SessionData>(cookies(), sessionOptions);
  if (!session.accessToken) {
    return NextResponse.json({ error: 'Not authenticated' }, { status: 401 });
  }

  const body = await request.json();
  const { fileId, fileName } = body as { fileId: string; fileName: string };

  if (!fileId || !fileName) {
    return NextResponse.json({ error: 'fileId and fileName required' }, { status: 400 });
  }

  let accessToken: string;
  try {
    accessToken = await getFreshToken(session);
    await session.save();
  } catch {
    return NextResponse.json({ error: 'Token refresh failed — please re-authenticate' }, { status: 401 });
  }

  // Get the parent folder ID before starting the job
  let parentFolderId: string;
  try {
    const res = await fetch(`https://api.box.com/2.0/files/${fileId}?fields=parent`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    if (!res.ok) throw new Error(`Box API error: ${res.status}`);
    const data = await res.json();
    parentFolderId = data.parent?.id;
    if (!parentFolderId) throw new Error('Could not determine parent folder');
  } catch (err) {
    return NextResponse.json({ error: `Failed to get file info: ${String(err)}` }, { status: 500 });
  }

  const jobId = crypto.randomUUID();
  createJob(jobId, parentFolderId, fileName, 'deposition_summary');

  runJob(jobId, accessToken, fileId, fileName, parentFolderId).catch((err) => {
    appendLog(jobId, `ERROR: ${String(err)}`);
    updateJob(jobId, { status: 'error', error: String(err) });
  });

  return NextResponse.json({ jobId });
}

async function runJob(
  jobId: string,
  accessToken: string,
  fileId: string,
  fileName: string,
  parentFolderId: string
) {
  updateJob(jobId, { status: 'running', progress: 'Detecting testimony pages...' });

  const tmpDir = path.join(os.tmpdir(), `box-depo-${jobId}`);
  await fs.mkdir(tmpDir, { recursive: true });

  try {
    const pythonDir = path.join(process.cwd(), 'python');
    const venvPython = path.join(process.cwd(), '.venv', 'bin', 'python3');
    const pythonBin = await fs.access(venvPython).then(() => venvPython).catch(() => 'python3');

    // Step 1 — depo_summary.py
    await runPython(pythonBin, [
      path.join(pythonDir, 'depo_summary.py'),
      '--file-id', fileId,
      '--token', accessToken,
      '--output-dir', tmpDir,
      '--model', process.env.BOX_AI_MODEL ?? 'google__gemini_2_5_pro',
    ], (line) => {
      if (!line.trim()) return;
      appendLog(jobId, line.trim());
      const m = line.match(/\[(\d+)\/(\d+)\]\s+page\s+(\d+)/i);
      if (m) updateJob(jobId, { progress: `Processing page ${m[3]} of ${m[2]}...` });
      if (line.includes('Auto-detected testimony start')) {
        updateJob(jobId, { progress: line.trim() });
      }
    });

    // Find the topics CSV
    const files = await fs.readdir(tmpDir);
    const topicsCsv = files.find((f) => f.endsWith('_depo_topics.csv'));
    if (!topicsCsv) throw new Error('depo_summary.py produced no topics CSV');

    const stem = topicsCsv.replace('_depo_topics.csv', '');
    const reportFile = path.join(tmpDir, `${stem}_summary.xlsx`);

    // Step 2 — depo_report.py
    updateJob(jobId, { progress: 'Generating summary report...' });
    await runPython(pythonBin, [
      path.join(pythonDir, 'depo_report.py'),
      '--input-file', path.join(tmpDir, topicsCsv),
      '--output-file', reportFile,
    ], (line) => {
      if (line.trim()) appendLog(jobId, line.trim());
    });

    // Step 3 — depo_pdf_generator.py
    const transcriptPdf = path.join(tmpDir, `${stem}_transcript.pdf`);
    const summarizedPdf = path.join(tmpDir, `${stem}_Summarized.pdf`);
    updateJob(jobId, { progress: 'Generating PDF summary...' });
    await runPython(pythonBin, [
      path.join(pythonDir, 'depo_pdf_generator.py'),
      '--transcript-path', transcriptPdf,
      '--csv-path', path.join(tmpDir, topicsCsv),
      '--output-path', summarizedPdf,
    ], (line) => {
      if (line.trim()) appendLog(jobId, line.trim());
    });

    // Step 4 — upload both files to Box
    updateJob(jobId, { progress: 'Uploading to Box...' });
    const dateStamp = new Date().toISOString().slice(0, 10);
    const baseName = fileName.replace(/\.pdf$/i, '');

    const xlsxBuffer = await fs.readFile(reportFile);
    const boxFileUrl = await uploadToBox(
      accessToken, parentFolderId,
      `${baseName}_summary_${dateStamp}.xlsx`,
      xlsxBuffer,
    );

    const pdfBuffer = await fs.readFile(summarizedPdf);
    const boxPdfUrl = await uploadToBox(
      accessToken, parentFolderId,
      `${baseName}_Summarized_${dateStamp}.pdf`,
      pdfBuffer,
    );

    updateJob(jobId, {
      status: 'complete',
      completedAt: new Date().toISOString(),
      boxFileUrl,
      boxPdfUrl,
      progress: 'Done',
    });
  } finally {
    await fs.rm(tmpDir, { recursive: true, force: true });
  }
}

function runPython(
  python: string,
  args: string[],
  onLine?: (line: string) => void
): Promise<void> {
  return new Promise((resolve, reject) => {
    const proc = spawn(python, args);
    const outputLines: string[] = [];

    proc.stdout.on('data', (chunk: Buffer) => {
      const lines = chunk.toString().split('\n');
      lines.forEach((l) => { outputLines.push(l); onLine?.(l); });
    });
    proc.stderr.on('data', (chunk: Buffer) => {
      const lines = chunk.toString().split('\n');
      lines.forEach((l) => { outputLines.push(l); onLine?.(l); });
    });
    proc.on('close', (code) => {
      if (code === 0) {
        resolve();
      } else {
        const lastLine = outputLines.filter((l) => l.trim()).pop() ?? '';
        const msg = lastLine.replace(/^(ERROR:|error:)/i, '').trim() || `Python exited with code ${code}`;
        reject(new Error(msg));
      }
    });
    proc.on('error', reject);
  });
}
