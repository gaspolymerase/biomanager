// The notebook editor: TipTap, saved as Markdown, edited live by everyone
// the page is shared with (collab.js), with the lab's own blocks
// (blocks/), step timers, the "/" menu, comment highlights and run mode.
//
// window.BiomanagerNotebook.mount({ element, page, me, onSave, ... })
// resolves to an object the page script drives (see
// app/static/notebook-page.js).

import { Editor, Extension } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import Link from '@tiptap/extension-link';
import Image from '@tiptap/extension-image';
import TaskList from '@tiptap/extension-task-list';
import TaskItem from '@tiptap/extension-task-item';
import Table from '@tiptap/extension-table';
import TableRow from '@tiptap/extension-table-row';
import TableHeader from '@tiptap/extension-table-header';
import TableCell from '@tiptap/extension-table-cell';
import Collaboration from '@tiptap/extension-collaboration';
import CollaborationCursor from '@tiptap/extension-collaboration-cursor';
import { Markdown } from 'tiptap-markdown';

import { MentionDecoration } from './extensions/MentionDecoration.js';
import { MentionSuggestion } from './extensions/MentionSuggestion.js';
import { MathInline } from './extensions/MathInline.js';
import { SlashMenu } from './extensions/SlashMenu.js';
import { CommentHighlights } from './extensions/CommentHighlights.js';
import { LabBlock } from './blocks/index.js';
import { mountRecipe } from './blocks/recipe.js';
import { StepTimers, timers } from './timers.js';
import { createToolbar } from './toolbar.js';
import { attachTableAutofill } from './table-autofill.js';
import { HttpSyncProvider, stateOf } from './collab.js';
import { appendLogLine, stampTime, taskItems } from './docops.js';
import { openRunMode } from './runmode.js';
import { uploadFile, uploadImage } from './commands.js';
import { debounce, personColor } from './util.js';

import './styles.css';
import './blocks.css';

const PLACEHOLDER = 'Write here… type / for sheets, recipes, calculators, diagrams and timers; @ and a name to link a record (a mouse, plasmid, antibody, sample…) or a person.';

const Shortcuts = Extension.create({
  name: 'nbShortcuts',
  addKeyboardShortcuts() {
    return {
      'Mod-Shift-;': () => { stampTime(this.editor); return true; },
      'Mod-Alt-t': () => { timers().ask(); return true; },
    };
  },
});

function extensionsFor({ provider, user, onCommentOpen }) {
  return [
    StarterKit.configure({
      heading: { levels: [1, 2, 3, 4] },
      codeBlock: { HTMLAttributes: { class: 'tiptap-code-block' } },
      history: false, // Collaboration keeps its own undo history
    }),
    Markdown.configure({ html: false, linkify: true, breaks: true, transformCopiedText: true, transformPastedText: true }),
    Placeholder.configure({
      placeholder: ({ node }) => (node.type.name === 'heading' ? `Heading ${node.attrs.level}` : PLACEHOLDER),
    }),
    Link.configure({ openOnClick: true, autolink: true, HTMLAttributes: { rel: 'noopener noreferrer', target: '_blank' } }),
    Image.configure({ allowBase64: false }),
    TaskList,
    TaskItem.configure({ nested: true }),
    Table.configure({ resizable: true, HTMLAttributes: { class: 'tiptap-table' } }),
    TableRow,
    TableHeader,
    TableCell,
    MentionDecoration,
    MentionSuggestion,
    LabBlock,
    MathInline,
    StepTimers,
    SlashMenu,
    CommentHighlights.configure({ onOpen: onCommentOpen }),
    Shortcuts,
    Collaboration.configure({ document: provider.doc }),
    CollaborationCursor.configure({ provider, user }),
  ];
}

function attachDropAndPaste(editor, element) {
  element.addEventListener('paste', (event) => {
    if (!editor.isEditable || !event.clipboardData) return;
    for (const item of event.clipboardData.items) {
      if (item.type && item.type.startsWith('image/')) {
        event.preventDefault();
        uploadImage(editor, item.getAsFile(), 'pasted image');
        return;
      }
    }
  });
  element.addEventListener('drop', (event) => {
    const files = event.dataTransfer && event.dataTransfer.files;
    if (!editor.isEditable || !files || !files.length) return;
    event.preventDefault();
    for (const file of files) {
      if (file.type && file.type.startsWith('image/')) uploadImage(editor, file);
      else uploadFile(editor, file);
    }
  });
}

async function mountOnce(options) {
  const { element, page, me } = options;
  const canEdit = page.role === 'owner' || page.role === 'edit';
  const user = { name: me.name || me.username, color: personColor(me.username) };
  let resetHandled = false;
  const provider = new HttpSyncProvider({
    pageId: page.id,
    canEdit,
    user,
    onPeers: options.onPeers,
    onStatus: options.onStatus,
    onMeta: options.onMeta,
    onReset: () => {
      if (resetHandled) return;
      resetHandled = true;
      options.onReset && options.onReset();
    },
  });
  provider.setUser(user);
  const { empty } = await provider.connect();

  element.innerHTML = '';
  let seeding = false;
  const editor = new Editor({
    element,
    editable: canEdit,
    extensions: extensionsFor({ provider, user, onCommentOpen: options.onCommentOpen }),
    autofocus: false,
  });

  if (empty) {
    seeding = true;
    editor.commands.setContent(page.body || '', false);
    seeding = false;
    if (canEdit) {
      const seeded = await provider.seed();
      if (!seeded) {
        // Someone else opened it at the same moment; take their state.
        provider.destroy();
        editor.destroy();
        return mountOnce(options);
      }
    }
  }
  provider.startLoop();
  if (canEdit) {
    // An assistant's notes, approved while this page was open somewhere:
    // added to the Log here, through the live document, like the Log button.
    provider.onInserts = (lines) => {
      for (const line of lines) {
        const text = line.via ? `${line.text} (via ${line.via})` : line.text;
        appendLogLine(editor, /^log$/i, 'Log', text, line.time ? { time: line.time } : {});
      }
    };
    provider.drainInserts();
  }

  // Save the Markdown for search, history and export. Changes typed here are
  // saved soon; changes from others are saved by them, and here only as a
  // fallback a little later.
  let lastSaved = page.body || '';
  const doSave = () => {
    if (!canEdit || editor.isDestroyed) return undefined;
    const markdown = editor.storage.markdown.getMarkdown();
    if (markdown === lastSaved) return undefined;
    lastSaved = markdown;
    return options.onSave && options.onSave(markdown, { gen: provider.gen, state: stateOf(provider.doc) });
  };
  const saveSoon = debounce(doSave, 900);
  const saveLater = debounce(doSave, 5000);
  editor.on('update', ({ transaction }) => {
    if (seeding) return;
    const remote = transaction.getMeta('y-sync$');
    if (remote && remote.isChangeOrigin) saveLater();
    else saveSoon();
    options.onChange && options.onChange();
  });

  const toolbar = canEdit ? createToolbar(editor, {
    extra: options.runnable === false ? [] : [{ id: 'run', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="6 4 20 12 6 20 6 4"/></svg>', label: 'Run the checklist step by step', exec: (e) => openRunMode(e, { title: options.getTitle ? options.getTitle() : '' }) }],
  }) : null;
  const autofill = attachTableAutofill(editor, element);
  attachDropAndPaste(editor, element);

  const flushOnHide = () => { if (document.visibilityState === 'hidden') saveSoon.flush(); };
  document.addEventListener('visibilitychange', flushOnHide);
  window.addEventListener('pagehide', () => saveSoon.flush());

  return {
    editor,
    provider,
    getMarkdown: () => editor.storage.markdown.getMarkdown(),
    // Save now; resolves once the server has it.
    flush: () => { saveSoon.cancel(); saveLater.cancel(); return Promise.resolve(doSave()); },
    setQuotes: (quotes) => editor.commands.setCommentQuotes(quotes),
    selectionText: () => {
      const { from, to } = editor.state.selection;
      return editor.state.doc.textBetween(from, to, ' ').trim();
    },
    scrollToQuote: (id) => {
      const mark = element.querySelector(`.nb-comment-mark[data-comment="${id}"]`);
      if (mark) {
        mark.scrollIntoView({ block: 'center', behavior: 'smooth' });
        mark.classList.add('is-flash');
        setTimeout(() => mark.classList.remove('is-flash'), 1600);
      }
    },
    hasSteps: () => taskItems(editor.state.doc).length > 0,
    runMode: () => openRunMode(editor, { title: options.getTitle ? options.getTitle() : '' }),
    addLog: (text) => appendLogLine(editor, /^log$/i, 'Log', text),
    setContent: (markdown) => editor.commands.setContent(markdown || ''),
    destroy: () => {
      saveSoon.flush();
      document.removeEventListener('visibilitychange', flushOnHide);
      autofill.destroy();
      toolbar && toolbar.destroy();
      provider.destroy();
      editor.destroy();
    },
  };
}

window.BiomanagerNotebook = {
  mount: mountOnce,
  timers,
  // The recipe editor on its own, for the Recipes page (static/notebook-library.js).
  recipe: mountRecipe,
};
