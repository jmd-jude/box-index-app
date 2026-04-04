'use client';

import { useEffect, useRef, useState } from 'react';

type Pipeline = 'document_index' | 'deposition_summary' | null;

interface AuthState {
  accessToken: string;
  userName: string;
  userEmail: string;
}

interface SelectedItem {
  id: string;
  name: string;
}

interface JobStatus {
  status: 'queued' | 'running' | 'complete' | 'error';
  pipeline?: 'document_index' | 'deposition_summary';
  progress?: string;
  log?: string[];
  boxFileUrl?: string;
  boxPdfUrl?: string;
  error?: string;
}

declare global {
  interface Window {
    Box: {
      ContentPicker: new () => {
        show: (
          folderId: string,
          token: string,
          options: {
            container: string;
            type: string;
            maxSelectable: number;
            extensions?: string[];
            logoUrl?: string;
            onChoose: (items: Array<{ id: string; name: string }>) => void;
            onCancel: () => void;
          }
        ) => void;
        hide: () => void;
      };
    };
  }
}

export default function Home() {
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [pipeline, setPipeline] = useState<Pipeline>(null);
  const [selectedItem, setSelectedItem] = useState<SelectedItem | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [generating, setGenerating] = useState(false);
  const [pickerKey, setPickerKey] = useState(0);
  const [enrichAI, setEnrichAI] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);

  // Check auth on mount
  useEffect(() => {
    fetch('/api/auth/token')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.accessToken) {
          setAuth({
            accessToken: data.accessToken,
            userName: data.userName ?? '',
            userEmail: data.userEmail ?? '',
          });
        }
      })
      .catch(() => {})
      .finally(() => setAuthLoading(false));
  }, []);

  // Init Box Content Picker once pipeline is selected
  useEffect(() => {
    if (!auth || !pipeline || !pickerRef.current) return;
    if (typeof window.Box === 'undefined') return;

    const picker = new window.Box.ContentPicker();
    const options: Parameters<typeof picker.show>[2] = {
      container: '#box-picker-container',
      type: pipeline === 'deposition_summary' ? 'file' : 'folder',
      maxSelectable: 1,
      logoUrl: '/logo.png',
      onChoose: (items: { id: string; name: string }[]) => {
        setSelectedItem({ id: items[0].id, name: items[0].name });
      },
      onCancel: () => {},
    };
    if (pipeline === 'deposition_summary') {
      options.extensions = ['pdf'];
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (picker as any).show('0', auth.accessToken, options);
  }, [auth, pipeline, pickerKey]);

  // Poll job status — immediate first fetch, then every 1s
  useEffect(() => {
    if (!jobId) return;

    async function poll() {
      const res = await fetch(`/api/job/${jobId}`);
      if (!res.ok) return;
      const data: JobStatus = await res.json();
      setJob(data);
      if (data.status === 'complete' || data.status === 'error') {
        clearInterval(interval);
        setGenerating(false);
      }
    }

    poll();
    const interval = setInterval(poll, 1000);
    return () => clearInterval(interval);
  }, [jobId]);

  async function handleGenerate() {
    if (!selectedItem || !pipeline) return;
    setGenerating(true);
    setJob({ status: 'queued', progress: 'Queuing job...' });

    const isDepo = pipeline === 'deposition_summary';
    const endpoint = isDepo ? '/api/depo' : '/api/generate';
    const body = isDepo
      ? { fileId: selectedItem.id, fileName: selectedItem.name }
      : { folderId: selectedItem.id, folderName: selectedItem.name, enrich: enrichAI };

    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const err = await res.json();
      setJob({ status: 'error', error: err.error ?? 'Unknown error' });
      setGenerating(false);
      return;
    }

    const { jobId: id } = await res.json();
    setJobId(id);
  }

  async function handleLogout() {
    await fetch('/api/auth/logout', { method: 'POST' });
    setAuth(null);
    setPipeline(null);
    setSelectedItem(null);
    setJobId(null);
    setJob(null);
  }

  function handleReset() {
    setPipeline(null);
    setSelectedItem(null);
    setJobId(null);
    setJob(null);
    setGenerating(false);
    setPickerKey((k) => k + 1);
  }

  function handleChangePipeline() {
    setPipeline(null);
    setSelectedItem(null);
    setPickerKey((k) => k + 1);
  }

  const isDepo = pipeline === 'deposition_summary';

  if (authLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="w-6 h-6 border-2 border-navy-700 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="text-white px-8 py-4 flex items-center justify-between" style={{ backgroundColor: '#669966' }}>
        <div className="flex items-center gap-3">
          <img src="/logo.png" alt="FPAmed" className="h-8 w-auto brightness-0 invert" />
          <p className="text-white/80 text-sm hidden sm:block">FPAmed Document Tools</p>
        </div>
        {auth && (
          <div className="flex items-center gap-4 text-sm">
            <span className="text-white/80">
              {auth.userName || auth.userEmail}
            </span>
            <button
              onClick={handleLogout}
              className="text-white/70 hover:text-white underline transition-colors"
            >
              Sign out
            </button>
          </div>
        )}
      </header>

      <main className={`flex-1 flex flex-col ${auth && pipeline && !job ? '' : 'items-center justify-center px-8 py-12'}`}>
        {/* ── State 1: Unauthenticated ── */}
        {!auth && (
          <div className="text-center max-w-md">
            <div className="mb-8">
              <div className="w-16 h-16 bg-slate-200 rounded-2xl flex items-center justify-center mx-auto mb-4">
                <svg className="w-8 h-8 text-slate-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                    d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
                </svg>
              </div>
              <p className="text-slate-500 text-sm">
                Generate document indexes and deposition summaries from Box.
              </p>
            </div>
            <a
              href="/api/auth/login"
              className="inline-block text-white px-6 py-3 rounded-lg font-medium transition-colors"
              style={{ backgroundColor: '#669966' }}
              onMouseOver={e => (e.currentTarget.style.backgroundColor = '#4f7a4f')}
              onMouseOut={e => (e.currentTarget.style.backgroundColor = '#669966')}
            >
              Connect Box Account
            </a>
          </div>
        )}

        {/* ── State 2: Authenticated — pipeline selection ── */}
        {auth && !pipeline && !job && (
          <div className="w-full max-w-2xl">
            <p className="text-slate-500 text-sm text-center mb-8">Select a tool to get started.</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {/* Document Index card */}
              <button
                onClick={() => setPipeline('document_index')}
                className="text-left border border-slate-200 rounded-xl p-6 hover:border-slate-400 hover:shadow-sm transition-all bg-white group"
              >
                <div className="w-10 h-10 rounded-lg flex items-center justify-center mb-4" style={{ backgroundColor: '#eaf2ea' }}>
                  <svg className="w-5 h-5" style={{ color: '#669966' }} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                      d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
                  </svg>
                </div>
                <h3 className="font-semibold text-slate-800 mb-2">Document Index</h3>
                <p className="text-slate-500 text-sm leading-relaxed">
                  Generate a formatted Excel index of all files in a Box folder, with page counts,
                  document dates, and duplicate detection.
                </p>
                <p className="mt-4 text-sm font-medium" style={{ color: '#669966' }}>
                  Select a folder →
                </p>
              </button>

              {/* Deposition Summary card */}
              <button
                onClick={() => setPipeline('deposition_summary')}
                className="text-left border border-slate-200 rounded-xl p-6 hover:border-slate-400 hover:shadow-sm transition-all bg-white group"
              >
                <div className="w-10 h-10 rounded-lg flex items-center justify-center mb-4" style={{ backgroundColor: '#eaf2ea' }}>
                  <svg className="w-5 h-5" style={{ color: '#669966' }} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                      d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                </div>
                <h3 className="font-semibold text-slate-800 mb-2">Deposition Summary</h3>
                <p className="text-slate-500 text-sm leading-relaxed">
                  Generate a structured page-by-page summary of a deposition transcript,
                  organized by topic with page citations.
                </p>
                <p className="mt-4 text-sm font-medium" style={{ color: '#669966' }}>
                  Select a transcript →
                </p>
              </button>
            </div>
          </div>
        )}

        {/* ── State 3: Authenticated, pipeline selected — item selection ── */}
        {auth && pipeline && !job && (
          <div className="w-full flex flex-col" style={{ height: 'calc(100vh - 73px)' }}>
            {/* Back link */}
            <div className="px-4 pt-3 pb-1">
              <button
                onClick={handleChangePipeline}
                className="text-sm text-slate-500 hover:text-slate-700 transition-colors"
              >
                ← Change tool
              </button>
            </div>

            {/* Box Content Picker */}
            <div
              key={pickerKey}
              id="box-picker-container"
              ref={pickerRef}
              className="flex-1 overflow-hidden bg-white"
            />

            {selectedItem && (
              <div className="mt-6 flex items-center justify-between text-white rounded-lg px-5 py-4" style={{ backgroundColor: '#669966' }}>
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 bg-white/10 rounded-full flex items-center justify-center flex-shrink-0">
                    <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                  </div>
                  <div>
                    <p className="text-xs text-white/70 uppercase tracking-wide font-medium mb-0.5">
                      {isDepo ? 'Selected transcript' : 'Selected folder'}
                    </p>
                    <p className="font-semibold">{selectedItem.name}</p>
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  {!isDepo && (
                    <label className="flex items-center gap-2 text-sm text-white/80 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={enrichAI}
                        onChange={(e) => setEnrichAI(e.target.checked)}
                        className="w-4 h-4 rounded accent-white cursor-pointer"
                      />
                      AI enrichment
                    </label>
                  )}
                  <button
                    onClick={handleGenerate}
                    disabled={generating}
                    className="bg-white text-slate-900 px-6 py-2.5 rounded-lg font-medium hover:bg-slate-100 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {isDepo ? 'Generate Summary' : 'Generate Index'}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── State 4: Job running / complete / error ── */}
        {auth && job && (
          <div className="w-full max-w-2xl text-center">
            {(job.status === 'queued' || job.status === 'running') && (
              <div>
                <div className="w-12 h-12 border-4 border-slate-200 rounded-full animate-spin mx-auto mb-6" style={{ borderTopColor: '#669966' }} />
                <h2 className="text-xl font-semibold text-slate-800 mb-2">
                  {isDepo ? 'Generating deposition summary…' : 'Generating index…'}
                </h2>
                <p className="text-slate-500 text-sm mb-4">{job.progress ?? 'Working…'}</p>
                {job.log && job.log.length > 0 && (
                  <div className="text-left bg-slate-900 rounded-lg p-4 font-mono text-xs text-slate-300 overflow-hidden">
                    {job.log.map((line, i) => (
                      <div key={i} className={i === job.log!.length - 1 ? 'text-white' : 'text-slate-500'}>
                        {line}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {job.status === 'complete' && (
              <div>
                <div className="w-12 h-12 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-6">
                  <svg className="w-6 h-6 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <h2 className="text-xl font-semibold text-slate-800 mb-2">
                  {isDepo ? 'Summary generated' : 'Index generated'}
                </h2>
                <p className="text-slate-500 text-sm mb-6">
                  {isDepo
                    ? 'The PDF summary and Excel report have been saved to the same Box folder as your transcript.'
                    : 'The Excel report has been saved to your Box folder.'}
                </p>
                <div className="flex flex-col sm:flex-row gap-3 justify-center">
                  {job.boxPdfUrl && (
                    <a
                      href={job.boxPdfUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-white px-6 py-2.5 rounded-lg font-medium transition-colors"
                      style={{ backgroundColor: '#669966' }}
                      onMouseOver={e => (e.currentTarget.style.backgroundColor = '#4f7a4f')}
                      onMouseOut={e => (e.currentTarget.style.backgroundColor = '#669966')}
                    >
                      Open PDF Summary
                    </a>
                  )}
                  {job.boxFileUrl && (
                    <a
                      href={job.boxFileUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-white px-6 py-2.5 rounded-lg font-medium transition-colors"
                      style={{ backgroundColor: isDepo && job.boxPdfUrl ? '#455A64' : '#669966' }}
                      onMouseOver={e => (e.currentTarget.style.backgroundColor = isDepo && job.boxPdfUrl ? '#37474F' : '#4f7a4f')}
                      onMouseOut={e => (e.currentTarget.style.backgroundColor = isDepo && job.boxPdfUrl ? '#455A64' : '#669966')}
                    >
                      {isDepo ? 'Open Excel' : 'Open in Box'}
                    </a>
                  )}
                  <button
                    onClick={handleReset}
                    className="border border-slate-300 text-slate-700 px-6 py-2.5 rounded-lg font-medium hover:bg-slate-100 transition-colors"
                  >
                    {isDepo ? 'Summarize another' : 'Generate another'}
                  </button>
                </div>
              </div>
            )}

            {job.status === 'error' && (
              <div>
                <div className="w-12 h-12 bg-red-100 rounded-full flex items-center justify-center mx-auto mb-6">
                  <svg className="w-6 h-6 text-red-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </div>
                <h2 className="text-xl font-semibold text-slate-800 mb-2">Something went wrong</h2>
                <p className="text-slate-500 text-sm mb-1">{job.error}</p>
                {job.log && job.log.length > 0 && (
                  <div className="text-left bg-slate-900 rounded-lg p-4 font-mono text-xs text-slate-300 overflow-auto max-h-48 mt-4 mb-2">
                    {job.log.slice(-30).map((line, i) => (
                      <div key={i} className={line.startsWith('ERROR') ? 'text-red-400' : 'text-slate-400'}>
                        {line}
                      </div>
                    ))}
                  </div>
                )}
                <button
                  onClick={handleReset}
                  className="mt-6 border border-slate-300 text-slate-700 px-6 py-2.5 rounded-lg font-medium hover:bg-slate-100 transition-colors"
                >
                  Try again
                </button>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
