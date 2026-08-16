/** Unified Inbox surface — a thin client over Package 2's trusted read model. */

(function () {
  window.inboxApp = function () {
    return {
      loading: true,
      entries: [],
      error: '',
      busyId: null,

      async initInbox() {
        document.title = 'Mycelos · ' + t('inbox.title');
        await this.loadInbox();
      },

      async loadInbox() {
        this.loading = true;
        this.error = '';
        try {
          const data = await MycelosAPI.get('/api/inbox');
          this.entries = Array.isArray(data?.entries) ? data.entries : [];
        } catch (_error) {
          this.entries = [];
          this.error = t('inbox.load_error');
        } finally {
          this.loading = false;
        }
      },

      countLabel() {
        return t('inbox.count').replace('{count}', String(this.entries.length));
      },

      noteHref(path) {
        return '/pages/knowledge.html?note=' + encodeURIComponent(path || '');
      },

      entryIcon(entry) {
        if (entry.kind === 'reminder') return 'notifications_active';
        if (entry.kind === 'overdue_task') return 'task_alt';
        if (entry.kind === 'failed_run') return 'sync_problem';
        return 'rule';
      },

      kindLabel(entry) {
        return entry.class === 'obligation' ? t('inbox.obligation') : t('inbox.decision');
      },

      supportedActions(entry) {
        const allowed = new Set(['accept', 'dismiss', 'done', 'snooze', 'retry']);
        return (Array.isArray(entry.actions) ? entry.actions : []).filter((action) => allowed.has(action.id));
      },

      actionLabel(actionId) {
        return t('inbox.action_' + actionId);
      },

      tomorrowDate() {
        const tomorrow = new Date();
        tomorrow.setDate(tomorrow.getDate() + 1);
        const year = tomorrow.getFullYear();
        const month = String(tomorrow.getMonth() + 1).padStart(2, '0');
        const day = String(tomorrow.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
      },

      async runAction(entry, actionId) {
        if (!entry || this.busyId) return;
        this.busyId = entry.id;
        this.error = '';
        try {
          if (actionId === 'accept' || actionId === 'dismiss') {
            const suggestionId = String(entry.id || '').split(':')[1];
            await MycelosAPI.post('/api/organizer/suggestions/' + encodeURIComponent(suggestionId) + '/' + actionId);
          } else if (actionId === 'done') {
            await MycelosAPI.post('/api/knowledge/notes/' + encodeURIComponent(entry.source?.path || '') + '/done');
          } else if (actionId === 'snooze') {
            await MycelosAPI.post(
              '/api/knowledge/notes/' + encodeURIComponent(entry.source?.path || '') + '/remind',
              { when: this.tomorrowDate() }
            );
          } else if (actionId === 'retry' && entry.kind === 'failed_run') {
            await MycelosAPI.post('/api/inbox/runs/' + encodeURIComponent(entry.source?.routine_key || '') + '/retry');
          } else if (actionId === 'retry') {
            await MycelosAPI.post('/api/inbox/notes/' + encodeURIComponent(entry.source?.path || '') + '/retry');
          }
          await this.loadInbox();
        } catch (_error) {
          this.error = t('inbox.action_error');
        } finally {
          this.busyId = null;
        }
      },
    };
  };
})();
