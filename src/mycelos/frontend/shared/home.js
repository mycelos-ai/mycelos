/** Home surface — Package 4a. */

(function () {
  const MODE_KEY = 'mycelos.home.mode';
  const EXPANDED_KEY = 'mycelos.home.expanded';

  function safeJson(value, fallback) {
    try {
      const parsed = JSON.parse(value);
      return parsed && typeof parsed === 'object' ? parsed : fallback;
    } catch (_error) {
      return fallback;
    }
  }

  function eventText(data) {
    if (typeof data === 'string') return data;
    if (!data || typeof data !== 'object') return '';
    return data.content || data.text || data.message || '';
  }

  window.homeApp = function () {
    return {
      query: '',
      mode: localStorage.getItem(MODE_KEY) === 'graph' ? 'graph' : 'tree',
      expanded: safeJson(localStorage.getItem(EXPANDED_KEY), {}),
      loading: true,
      searching: false,
      barFocused: false,
      barError: '',
      searchResults: [],
      searchSequence: 0,
      graph: { nodes: [], edges: [], stats: { notes: 0, links: 0 } },
      nodeById: {},
      parentById: {},
      topicChildren: {},
      topicCounts: {},
      placements: {},
      today: { loaded: false, inboxCount: 0, dueEntries: [] },
      rootNotesShown: 200,
      answer: {
        open: false,
        thinking: false,
        question: '',
        text: '',
        error: '',
        citations: [],
        sessionId: null,
      },
      capture: { saving: false, message: '', error: false, timer: null },

      async initHome() {
        document.title = 'Mycelos · ' + t('home.title');
        await this.loadHome();
      },

      async loadHome(showLoading = true) {
        if (showLoading) this.loading = true;
        const [graphResult, inboxCountResult, inboxResult, placementsResult] = await Promise.allSettled([
          MycelosAPI.get('/api/knowledge/graph'),
          MycelosAPI.get('/api/inbox/count'),
          MycelosAPI.get('/api/inbox'),
          MycelosAPI.get('/api/inbox/placements?limit=500'),
        ]);

        if (graphResult.status === 'fulfilled') {
          const data = graphResult.value || {};
          this.graph = {
            nodes: Array.isArray(data.nodes) ? data.nodes : [],
            edges: Array.isArray(data.edges) ? data.edges : [],
            stats: data.stats || {},
          };
          this.buildTreeIndex();
        } else {
          this.barError = t('home.load_error');
        }

        if (inboxCountResult.status === 'fulfilled') {
          this.today.inboxCount = Number(inboxCountResult.value?.count || 0);
        }

        if (inboxResult.status === 'fulfilled') {
          const entries = Array.isArray(inboxResult.value?.entries) ? inboxResult.value.entries : [];
          this.today.dueEntries = entries.filter((entry) =>
            entry && (entry.kind === 'reminder' || entry.kind === 'overdue_task')
          );
        }

        if (placementsResult.status === 'fulfilled') {
          const rows = Array.isArray(placementsResult.value?.placements) ? placementsResult.value.placements : [];
          this.placements = rows.reduce((acc, row) => {
            if (row && row.path) acc[row.path] = true;
            return acc;
          }, {});
        }

        this.today.loaded = true;
        if (showLoading) this.loading = false;
      },

      buildTreeIndex() {
        this.nodeById = {};
        this.parentById = {};
        this.topicChildren = {};

        for (const node of this.graph.nodes) {
          if (!node || !node.id) continue;
          this.nodeById[node.id] = node;
          if (node.type === 'topic') this.topicChildren[node.id] = [];
        }

        for (const edge of this.graph.edges) {
          if (!edge || edge.kind !== 'parent' || !edge.source || !edge.target) continue;
          this.parentById[edge.source] = edge.target;
          if (this.nodeById[edge.source]?.type === 'topic' && this.nodeById[edge.target]?.type === 'topic') {
            if (!this.topicChildren[edge.target]) this.topicChildren[edge.target] = [];
            this.topicChildren[edge.target].push(edge.source);
          }
        }

        for (const id of Object.keys(this.topicChildren)) {
          this.topicChildren[id].sort((a, b) =>
            (this.nodeById[a]?.title || '').localeCompare(this.nodeById[b]?.title || '', undefined, { sensitivity: 'base' })
          );
        }

        const memo = {};
        const countNotes = (topicId, visiting) => {
          if (memo[topicId] !== undefined) return memo[topicId];
          if (visiting.has(topicId)) return 0;
          visiting.add(topicId);
          let count = 0;
          for (const node of this.graph.nodes) {
            if (this.parentById[node.id] !== topicId) continue;
            if (node.type === 'topic') count += countNotes(node.id, visiting);
            else count += 1;
          }
          visiting.delete(topicId);
          memo[topicId] = count;
          return count;
        };

        this.topicCounts = {};
        for (const id of Object.keys(this.topicChildren)) {
          this.topicCounts[id] = countNotes(id, new Set());
        }
      },

      rootTopics() {
        return this.graph.nodes
          .filter((node) => node.type === 'topic' && this.nodeById[this.parentById[node.id]]?.type !== 'topic')
          .sort((a, b) => (a.title || '').localeCompare(b.title || '', undefined, { sensitivity: 'base' }));
      },

      rootNotes() {
        return this.graph.nodes
          .filter((node) => node.type !== 'topic' && !this.nodeById[this.parentById[node.id]])
          .sort((a, b) => (a.title || '').localeCompare(b.title || '', undefined, { sensitivity: 'base' }));
      },

      treeRows() {
        if (this.query.trim()) return this.searchTreeRows();

        const rows = [];
        const walk = (node, depth, visiting) => {
          if (!node || visiting.has(node.id)) return;
          visiting.add(node.id);
          const children = this.topicChildren[node.id] || [];
          rows.push({
            key: 'topic:' + node.id,
            id: node.id,
            title: node.title || node.id,
            type: 'topic',
            depth,
            count: this.topicCounts[node.id] || 0,
            hasChildren: children.length > 0,
            uncertain: false,
          });
          if (this.expanded[node.id]) {
            for (const childId of children) walk(this.nodeById[childId], depth + 1, visiting);
          }
          visiting.delete(node.id);
        };

        for (const root of this.rootTopics()) walk(root, 0, new Set());

        const unfiled = this.rootNotes();
        if (unfiled.length) {
          rows.push({
            key: 'topic:__unfiled__',
            id: '__unfiled__',
            title: t('home.unfiled'),
            type: 'topic',
            depth: 0,
            count: unfiled.length,
            hasChildren: true,
            uncertain: false,
            synthetic: true,
          });
          if (this.expanded.__unfiled__) {
            for (const node of unfiled.slice(0, this.rootNotesShown)) {
              rows.push({
                key: 'unfiled:' + node.id,
                id: node.id,
                title: node.title || node.id,
                type: node.type || 'note',
                depth: 1,
                count: 0,
                hasChildren: false,
                uncertain: !!this.placements[node.id],
              });
            }
          }
        }
        return rows;
      },

      searchTreeRows() {
        const rows = [];
        const seen = new Set();

        for (const result of this.searchResults) {
          if (!result || !result.path) continue;
          const node = this.nodeById[result.path] || {
            id: result.path,
            title: result.title || result.path,
            type: result.type || 'note',
          };

          const chain = [];
          const chainSeen = new Set();
          let parentId = this.parentById[node.id];
          while (parentId && !chainSeen.has(parentId)) {
            chainSeen.add(parentId);
            const parent = this.nodeById[parentId];
            if (!parent || parent.type !== 'topic') break;
            chain.unshift(parent);
            parentId = this.parentById[parentId];
          }

          chain.forEach((topic, depth) => {
            const key = 'search-topic:' + topic.id;
            if (seen.has(key)) return;
            seen.add(key);
            rows.push({
              key,
              id: topic.id,
              title: topic.title || topic.id,
              type: 'topic',
              depth,
              count: this.topicCounts[topic.id] || 0,
              hasChildren: false,
              uncertain: false,
            });
          });

          const rowKey = 'search-note:' + node.id;
          if (seen.has(rowKey)) continue;
          seen.add(rowKey);
          rows.push({
            key: rowKey,
            id: node.id,
            title: node.title || node.id,
            type: node.type || 'note',
            depth: chain.length,
            count: 0,
            hasChildren: false,
            uncertain: !!this.placements[node.id],
          });
        }

        return rows;
      },

      async searchQuery() {
        const value = this.query.trim();
        const sequence = ++this.searchSequence;
        this.barError = '';
        if (!value) {
          this.searchResults = [];
          this.searching = false;
          return;
        }

        this.searching = true;
        try {
          const results = await MycelosAPI.get('/api/knowledge/notes?query=' + encodeURIComponent(value) + '&limit=50');
          if (sequence !== this.searchSequence) return;
          this.searchResults = Array.isArray(results) ? results : [];
        } catch (_error) {
          if (sequence !== this.searchSequence) return;
          this.searchResults = [];
          this.barError = t('home.search_error');
        } finally {
          if (sequence === this.searchSequence) this.searching = false;
        }
      },

      async ensureSearchResults(value) {
        if (this.query.trim() !== value) return;
        if (this.searchResults.length && !this.searching) return;
        try {
          const results = await MycelosAPI.get('/api/knowledge/notes?query=' + encodeURIComponent(value) + '&limit=50');
          if (this.query.trim() === value) {
            this.searchResults = Array.isArray(results) ? results : [];
          }
        } catch (_error) {
          this.barError = t('home.search_error');
        }
      },

      handleEnter(event) {
        if (event.isComposing) return;
        if (event.shiftKey) this.keepQuery();
        else this.askQuery();
      },

      async askQuery() {
        const question = this.query.trim();
        if (!question || this.answer.thinking) return;
        await this.ensureSearchResults(question);

        this.answer = {
          open: true,
          thinking: true,
          question,
          text: '',
          error: '',
          citations: this.searchResults.slice(0, 5).map((row) => ({
            path: row.path,
            title: row.title || row.path,
          })),
          sessionId: null,
        };

        try {
          await MycelosAPI.stream('/api/chat', { message: question }, (type, data) => {
            if (type === 'session' && data?.session_id) {
              this.answer.sessionId = data.session_id;
              return;
            }
            if (type === 'text' || type === 'system-response') {
              const text = eventText(data);
              if (text) this.answer.text = text;
              return;
            }
            if (type === 'error') {
              this.answer.error = eventText(data) || t('home.ask_error');
              return;
            }
            if (type === 'done') this.answer.thinking = false;
          });
        } catch (_error) {
          this.answer.error = t('home.ask_error');
        } finally {
          this.answer.thinking = false;
        }
      },

      async keepQuery() {
        const text = this.query.trim();
        if (!text || this.capture.saving) return;
        await this.captureText(text, this.captureTitle(text));
        if (!this.capture.error) {
          this.query = '';
          this.searchResults = [];
          this.searchSequence += 1;
          this.$nextTick(() => this.focusBar());
        }
      },

      async keepAnswer() {
        if (!this.answer.text || this.capture.saving) return;
        const content = this.answer.question + '\n\n' + this.answer.text;
        await this.captureText(content, this.captureTitle(this.answer.question));
      },

      captureTitle(text) {
        const first = String(text || '').split(/\r?\n/)[0].trim();
        if (!first) return t('home.untitled_capture');
        return first.length > 80 ? first.slice(0, 77) + '…' : first;
      },

      async captureText(content, title) {
        this.capture.saving = true;
        this.showCaptureMessage(t('home.filing'), false, 0);
        try {
          const created = await MycelosAPI.post('/api/knowledge/notes', { title, content });
          this.showCaptureMessage(this.captureMessage(created), false, 3200);
          await this.loadHome(false);
        } catch (_error) {
          this.showCaptureMessage(t('home.keep_error'), true, 4800);
        } finally {
          this.capture.saving = false;
        }
      },

      captureMessage(created) {
        const parent = String(created?.parent_path || '').trim();
        if (!parent) return t('home.kept');
        const location = parent === 'notes'
          ? t('home.capture_notes_location')
          : parent;
        const message = t('home.kept_location').replace('{location}', location);
        return created?.organizer_state === 'pending'
          ? message + ' ' + t('home.organizer_pending')
          : message;
      },

      showCaptureMessage(message, error, timeout) {
        if (this.capture.timer) window.clearTimeout(this.capture.timer);
        this.capture.message = message;
        this.capture.error = error;
        if (timeout) {
          this.capture.timer = window.setTimeout(() => {
            this.capture.message = '';
            this.capture.error = false;
          }, timeout);
        }
      },

      handleGlobalKeydown(event) {
        if ((event.metaKey || event.ctrlKey) && String(event.key).toLowerCase() === 'k') {
          event.preventDefault();
          this.focusBar();
          return;
        }
        if (event.key !== 'Escape') return;
        if (this.answer.open) {
          event.preventDefault();
          this.closeAnswer();
          return;
        }
        if (this.query) {
          event.preventDefault();
          this.query = '';
          this.searchResults = [];
          this.searchSequence += 1;
          this.barError = '';
          return;
        }
        if (document.activeElement === this.$refs.omnibox) {
          event.preventDefault();
          this.$refs.omnibox.blur();
        }
      },

      focusBar() {
        this.$nextTick(() => {
          if (this.$refs.omnibox) this.$refs.omnibox.focus();
        });
      },

      closeAnswer() {
        this.answer.open = false;
        this.answer.thinking = false;
      },

      setMode(mode) {
        this.mode = mode === 'graph' ? 'graph' : 'tree';
        localStorage.setItem(MODE_KEY, this.mode);
      },

      toggleTopic(id) {
        this.expanded[id] = !this.expanded[id];
        this.expanded = { ...this.expanded };
        localStorage.setItem(EXPANDED_KEY, JSON.stringify(this.expanded));
      },

      openRow(row) {
        if (!row?.id) return;
        if (row.synthetic) this.toggleTopic(row.id);
        else if (row.type === 'topic') {
          window.location.href = '/pages/knowledge.html?topic=' + encodeURIComponent(row.id);
        } else window.location.href = this.noteHref(row.id);
      },

      noteHref(path) {
        return '/pages/knowledge.html?note=' + encodeURIComponent(path || '');
      },

      rowIcon(row) {
        if (row.type === 'topic') return 'folder';
        if (row.type === 'task') return 'task_alt';
        if (row.type === 'document') return 'description';
        return 'notes';
      },

      matchHint() {
        if (this.searchResults.length === 1) return t('home.match_count_one');
        return t('home.match_count_many').replace('{count}', String(this.searchResults.length));
      },

      rootNotesRemaining() {
        return Math.max(0, this.rootNotes().length - this.rootNotesShown);
      },

      moreRootNotes() {
        this.rootNotesShown += 200;
      },

      moreRootNotesLabel() {
        return t('home.more_notes').replace('{count}', String(this.rootNotesRemaining()));
      },

      inboxLabel() {
        return t('home.inbox_count').replace('{count}', String(this.today.inboxCount));
      },

      dueLabel() {
        return t('home.due_count').replace('{count}', String(this.today.dueEntries.length));
      },

      dueHref() {
        return '/pages/inbox.html';
      },

      converseHref() {
        return this.answer.sessionId
          ? '/pages/chat.html?session=' + encodeURIComponent(this.answer.sessionId)
          : '/pages/chat.html?prefill=' + encodeURIComponent(this.answer.question || this.query);
      },

      brainStateLabel() {
        return t('home.brain_state')
          .replace('{notes}', String(this.graph.stats?.notes || this.graph.nodes.length || 0))
          .replace('{links}', String(this.graph.stats?.links || this.graph.edges.length || 0));
      },
    };
  };
})();
