export interface Job {
  id: string;
  status: 'queued' | 'running' | 'complete' | 'error';
  pipeline: 'document_index' | 'deposition_summary';
  folderId: string;
  folderName: string;
  createdAt: string;
  completedAt?: string;
  boxFileUrl?: string;
  boxPdfUrl?: string;
  error?: string;
  progress?: string;
  log?: string[];
}

// Attach to global so the Map is shared across Next.js route module instances in dev
declare global {
  // eslint-disable-next-line no-var
  var __jobs: Map<string, Job> | undefined;
}
const jobs: Map<string, Job> = global.__jobs ?? (global.__jobs = new Map());

export function createJob(
  id: string,
  folderId: string,
  folderName: string,
  pipeline: Job['pipeline']
): Job {
  const job: Job = {
    id,
    status: 'queued',
    pipeline,
    folderId,
    folderName,
    createdAt: new Date().toISOString(),
    log: [],
  };
  jobs.set(id, job);
  return job;
}

export function getJob(id: string): Job | undefined {
  return jobs.get(id);
}

export function updateJob(id: string, updates: Partial<Job>): void {
  const job = jobs.get(id);
  if (job) jobs.set(id, { ...job, ...updates });
}

export function appendLog(id: string, line: string): void {
  const job = jobs.get(id);
  if (!job) return;
  const log = [...(job.log ?? []), line].slice(-200);
  jobs.set(id, { ...job, log });
}
