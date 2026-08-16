/** Accessible Home graph workbench. */

(function () {
  const GRAPH_TOPICS_KEY = 'mycelos.home.graph.topics';
  const GRAPH_VIEWPORT_KEY = 'mycelos.home.graph.viewport';
  const GRAPH_BATCH_SIZE = 50;
  const GRAPH_WIDTH = 2400;
  const GRAPH_HEIGHT = 1600;

  function safeObject(value, fallback) {
    try {
      const parsed = JSON.parse(value);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : fallback;
    } catch (_error) {
      return fallback;
    }
  }

  function clamp(value, minimum, maximum) {
    return Math.min(maximum, Math.max(minimum, value));
  }

  function hashPath(path) {
    let value = 2166136261;
    for (const char of String(path || '')) {
      value ^= char.charCodeAt(0);
      value = Math.imul(value, 16777619);
    }
    return value >>> 0;
  }

  function apiPath(path) {
    return String(path || '').split('/').map(encodeURIComponent).join('/');
  }

  function mobileGraphFallback() {
    return window.innerWidth <= 600;
  }

  function storedDesktopMode() {
    return localStorage.getItem('mycelos.home.mode') === 'tree' ? 'tree' : 'graph';
  }

  function normalizedPositions(value) {
    const result = {};
    const rows = Array.isArray(value)
      ? value
      : Object.entries(value && typeof value === 'object' ? value : {}).map(([path, position]) => ({
        path,
        ...(position && typeof position === 'object' ? position : {}),
      }));
    for (const row of rows) {
      const path = row?.path || row?.id;
      const x = Number(row?.x);
      const y = Number(row?.y);
      if (path && Number.isFinite(x) && Number.isFinite(y)) result[path] = { x, y };
    }
    return result;
  }

  window.homeGraphInitialMode = function () {
    return mobileGraphFallback() ? 'tree' : storedDesktopMode();
  };

  window.homeGraphMixin = function () {
    const viewport = safeObject(localStorage.getItem(GRAPH_VIEWPORT_KEY), {});
    return {
      graphMobile: mobileGraphFallback(),
      graphPreferredMode: storedDesktopMode(),
      graphOpenTopics: safeObject(localStorage.getItem(GRAPH_TOPICS_KEY), {}),
      graphChildLimits: {},
      graphChildrenIndex: {},
      graphRootIds: [],
      graphRootLimit: GRAPH_BATCH_SIZE,
      graphPositions: {},
      graphPan: {
        x: Number.isFinite(Number(viewport.x)) ? Number(viewport.x) : 0,
        y: Number.isFinite(Number(viewport.y)) ? Number(viewport.y) : 0,
      },
      graphZoom: Number.isFinite(Number(viewport.zoom))
        ? clamp(Number(viewport.zoom), 0.45, 1.8)
        : 1,
      graphSelectedId: null,
      graphKeyboardTargetId: '',
      graphDraggingId: null,
      graphDropTargetId: null,
      graphInvalidDropTargetId: null,
      graphPointer: null,
      graphSuppressClick: false,
      graphUndo: null,
      graphNotice: { message: '', error: false },

      setupHomeGraph(data) {
        this.graphPositions = normalizedPositions(data?.positions);
        this.graphRootLimit = GRAPH_BATCH_SIZE;
        this.buildGraphChildrenIndex();
        const validOpenTopics = {};
        for (const id of Object.keys(this.graphOpenTopics)) {
          if (this.nodeById[id]?.type !== 'topic') continue;
          validOpenTopics[id] = true;
          this.graphChildLimits[id] = GRAPH_BATCH_SIZE;
        }
        this.graphOpenTopics = validOpenTopics;
      },

      buildGraphChildrenIndex() {
        this.graphChildrenIndex = {};
        for (const node of this.graph.nodes) {
          const parent = this.parentById[node?.id];
          if (!node?.id || !parent) continue;
          if (!this.graphChildrenIndex[parent]) this.graphChildrenIndex[parent] = [];
          this.graphChildrenIndex[parent].push(node.id);
        }
        for (const parent of Object.keys(this.graphChildrenIndex)) {
          this.graphChildrenIndex[parent].sort((left, right) =>
            (this.nodeById[left]?.title || left).localeCompare(
              this.nodeById[right]?.title || right,
              undefined,
              { sensitivity: 'base' }
            )
          );
        }
        this.graphRootIds = this.rootTopics().map((node) => node.id);
      },

      handleHomeResize() {
        const mobile = mobileGraphFallback();
        if (mobile === this.graphMobile) return;
        this.graphMobile = mobile;
        this.mode = mobile ? 'tree' : this.graphPreferredMode;
      },

      graphVisibleIds() {
        const visible = new Set();
        const visitTopic = (id, visiting) => {
          if (!id || visiting.has(id)) return;
          visiting.add(id);
          visible.add(id);
          if (this.graphOpenTopics[id]) {
            const limit = this.graphChildLimits[id] || GRAPH_BATCH_SIZE;
            const children = (this.graphChildrenIndex[id] || []).slice(0, limit);
            for (const childId of children) {
              visible.add(childId);
              if (this.nodeById[childId]?.type === 'topic' && this.graphOpenTopics[childId]) {
                visitTopic(childId, visiting);
              }
            }
          }
          visiting.delete(id);
        };

        for (const id of this.graphRootIds.slice(0, this.graphRootLimit)) visitTopic(id, new Set());

        if (this.query.trim()) {
          for (const result of this.searchResults) {
            if (!result?.path || !this.nodeById[result.path]) continue;
            visible.add(result.path);
            const seen = new Set();
            let parent = this.parentById[result.path];
            while (parent && !seen.has(parent)) {
              seen.add(parent);
              if (this.nodeById[parent]?.type !== 'topic') break;
              visible.add(parent);
              parent = this.parentById[parent];
            }
          }
        }

        if (this.graphSelectedId && this.nodeById[this.graphSelectedId]) {
          visible.add(this.graphSelectedId);
          for (const edge of this.graph.edges) {
            if (!edge || edge.kind === 'parent') continue;
            if (edge.source === this.graphSelectedId && this.nodeById[edge.target]) visible.add(edge.target);
            if (edge.target === this.graphSelectedId && this.nodeById[edge.source]) visible.add(edge.source);
          }
        }
        return visible;
      },

      graphVisibleNodes() {
        const visible = this.graphVisibleIds();
        return this.graph.nodes.filter((node) => node?.id && visible.has(node.id));
      },

      graphVisibleParentEdges() {
        const visible = this.graphVisibleIds();
        return this.graph.edges.filter((edge) =>
          edge?.kind === 'parent' && visible.has(edge.source) && visible.has(edge.target)
        );
      },

      graphSelectedRelationEdges() {
        if (!this.graphSelectedId) return [];
        const visible = this.graphVisibleIds();
        return this.graph.edges.filter((edge) =>
          edge && edge.kind !== 'parent'
          && (edge.source === this.graphSelectedId || edge.target === this.graphSelectedId)
          && visible.has(edge.source)
          && visible.has(edge.target)
        );
      },

      graphSelectedRelations() {
        return this.graphSelectedRelationEdges().map((edge, index) => {
          const otherId = edge.source === this.graphSelectedId ? edge.target : edge.source;
          const other = this.nodeById[otherId];
          return {
            key: `${edge.kind}:${edge.source}:${edge.target}:${index}`,
            id: otherId,
            label: other?.title || otherId,
            kind: edge.kind,
          };
        });
      },

      graphMissingSearchResults() {
        if (!this.query.trim()) return [];
        return this.searchResults.filter((result) => result?.path && !this.nodeById[result.path]);
      },

      graphResultHref(result) {
        if (result?.type === 'topic') {
          return `/pages/knowledge.html?topic=${encodeURIComponent(result.path || '')}`;
        }
        return this.noteHref(result?.path || '');
      },

      graphSearchMatches() {
        return new Set(
          this.searchResults
            .filter((result) => result?.path && this.nodeById[result.path])
            .map((result) => result.path)
        );
      },

      graphSearchRelatedIds() {
        const related = this.graphSearchMatches();
        for (const id of [...related]) {
          const seen = new Set();
          let parent = this.parentById[id];
          while (parent && !seen.has(parent)) {
            seen.add(parent);
            if (this.nodeById[parent]?.type !== 'topic') break;
            related.add(parent);
            parent = this.parentById[parent];
          }
        }
        return related;
      },

      graphNodeClasses(node) {
        const matches = this.graphSearchMatches();
        const related = this.graphSearchRelatedIds();
        return {
          'is-topic': node.type === 'topic',
          'is-selected': this.graphSelectedId === node.id,
          'is-match': matches.has(node.id),
          'is-dimmed': !!this.query.trim() && !related.has(node.id),
          'is-dragging': this.graphDraggingId === node.id,
          'is-drop-target': this.graphDropTargetId === node.id,
          'is-invalid-target': this.graphInvalidDropTargetId === node.id,
        };
      },

      graphNodeLabel(node) {
        const kind = node.type === 'topic' ? t('home.topic') : t('home.note');
        return `${node.title || node.id}, ${kind}`;
      },

      graphChildCount(id) {
        if (id === '__root__') return this.graphRootIds.length;
        return (this.graphChildrenIndex[id] || []).length;
      },

      toggleGraphTopic(id) {
        if (!id || this.nodeById[id]?.type !== 'topic') return;
        if (this.graphOpenTopics[id]) delete this.graphOpenTopics[id];
        else {
          this.graphOpenTopics[id] = true;
          this.graphChildLimits[id] = GRAPH_BATCH_SIZE;
        }
        this.graphOpenTopics = { ...this.graphOpenTopics };
        localStorage.setItem(GRAPH_TOPICS_KEY, JSON.stringify(this.graphOpenTopics));
      },

      graphMoreBatches() {
        const batches = Object.keys(this.graphOpenTopics)
          .map((id) => ({
            id,
            title: this.nodeById[id]?.title || id,
            remaining: Math.max(0, this.graphChildCount(id) - (this.graphChildLimits[id] || GRAPH_BATCH_SIZE)),
          }))
          .filter((row) => row.remaining > 0);
        const rootRemaining = Math.max(0, this.graphRootIds.length - this.graphRootLimit);
        if (rootRemaining) batches.unshift({ id: '__root__', title: t('home.topics'), remaining: rootRemaining });
        return batches;
      },

      moreGraphChildren(id) {
        if (id === '__root__') {
          this.graphRootLimit += GRAPH_BATCH_SIZE;
          return;
        }
        this.graphChildLimits[id] = (this.graphChildLimits[id] || GRAPH_BATCH_SIZE) + GRAPH_BATCH_SIZE;
        this.graphChildLimits = { ...this.graphChildLimits };
      },

      graphMoreLabel(batch) {
        const count = Math.min(GRAPH_BATCH_SIZE, batch.remaining);
        return t('home.graph_more')
          .replace('{count}', String(count))
          .replace('{title}', batch.title)
          .replace('{remaining}', String(batch.remaining));
      },

      graphPosition(id, visiting = new Set()) {
        if (this.graphPositions[id]) return this.graphPositions[id];
        if (visiting.has(id)) return { x: GRAPH_WIDTH / 2, y: GRAPH_HEIGHT / 2 };
        visiting.add(id);

        const parentId = this.parentById[id];
        let position;
        if (parentId && this.nodeById[parentId]) {
          const parent = this.graphPosition(parentId, visiting);
          const hash = hashPath(id);
          const angle = ((hash % 360) * Math.PI) / 180;
          const radius = 170 + (hash % 4) * 24;
          position = {
            x: clamp(Math.round(parent.x + Math.cos(angle) * radius), 100, GRAPH_WIDTH - 100),
            y: clamp(Math.round(parent.y + Math.sin(angle) * radius), 80, GRAPH_HEIGHT - 80),
          };
        } else {
          const hash = hashPath(id);
          const knownRootIndex = this.graphRootIds.indexOf(id);
          const rootIndex = knownRootIndex >= 0
            ? knownRootIndex
            : this.graphRootIds.length + (hash % 50);
          position = {
            x: 300 + (rootIndex % 5) * 370,
            y: 220 + Math.floor(rootIndex / 5) * 270 + (hash % 31),
          };
        }
        visiting.delete(id);
        this.graphPositions[id] = position;
        return position;
      },

      graphNodeStyle(node) {
        const position = this.graphPosition(node.id);
        return `left:${position.x}px;top:${position.y}px`;
      },

      graphStageStyle() {
        return `transform:translate(${this.graphPan.x}px, ${this.graphPan.y}px) scale(${this.graphZoom})`;
      },

      graphStagePoint(clientX, clientY) {
        const canvas = this.$refs.graphCanvas;
        const box = canvas?.getBoundingClientRect();
        if (!box) return { x: 0, y: 0 };
        return {
          x: (clientX - box.left - this.graphPan.x) / this.graphZoom,
          y: (clientY - box.top - this.graphPan.y) / this.graphZoom,
        };
      },

      saveGraphViewport() {
        localStorage.setItem(GRAPH_VIEWPORT_KEY, JSON.stringify({
          x: this.graphPan.x,
          y: this.graphPan.y,
          zoom: this.graphZoom,
        }));
      },

      zoomGraph(delta) {
        this.graphZoom = clamp(this.graphZoom + delta, 0.45, 1.8);
        this.saveGraphViewport();
      },

      zoomGraphAt(event) {
        const canvas = this.$refs.graphCanvas;
        const box = canvas?.getBoundingClientRect();
        if (!box) return;
        const oldZoom = this.graphZoom;
        const nextZoom = clamp(oldZoom + (event.deltaY < 0 ? 0.1 : -0.1), 0.45, 1.8);
        const worldX = (event.clientX - box.left - this.graphPan.x) / oldZoom;
        const worldY = (event.clientY - box.top - this.graphPan.y) / oldZoom;
        this.graphZoom = nextZoom;
        this.graphPan = {
          x: event.clientX - box.left - worldX * nextZoom,
          y: event.clientY - box.top - worldY * nextZoom,
        };
        this.saveGraphViewport();
      },

      fitGraph() {
        const canvas = this.$refs.graphCanvas;
        const nodes = this.graphVisibleNodes();
        if (!canvas || !nodes.length) return;
        const positions = nodes.map((node) => this.graphPosition(node.id));
        const minimumX = Math.min(...positions.map((position) => position.x)) - 120;
        const maximumX = Math.max(...positions.map((position) => position.x)) + 120;
        const minimumY = Math.min(...positions.map((position) => position.y)) - 90;
        const maximumY = Math.max(...positions.map((position) => position.y)) + 90;
        const zoom = clamp(Math.min(
          canvas.clientWidth / Math.max(1, maximumX - minimumX),
          canvas.clientHeight / Math.max(1, maximumY - minimumY)
        ), 0.45, 1.4);
        this.graphZoom = zoom;
        this.graphPan = {
          x: (canvas.clientWidth - (minimumX + maximumX) * zoom) / 2,
          y: (canvas.clientHeight - (minimumY + maximumY) * zoom) / 2,
        };
        this.saveGraphViewport();
      },

      handleGraphKeydown(event) {
        const amount = event.shiftKey ? 100 : 40;
        if (event.key === 'ArrowLeft') this.graphPan = { ...this.graphPan, x: this.graphPan.x + amount };
        else if (event.key === 'ArrowRight') this.graphPan = { ...this.graphPan, x: this.graphPan.x - amount };
        else if (event.key === 'ArrowUp') this.graphPan = { ...this.graphPan, y: this.graphPan.y + amount };
        else if (event.key === 'ArrowDown') this.graphPan = { ...this.graphPan, y: this.graphPan.y - amount };
        else if (event.key === '+' || event.key === '=') this.zoomGraph(0.15);
        else if (event.key === '-') this.zoomGraph(-0.15);
        else if (event.key === '0' || String(event.key).toLowerCase() === 'f') this.fitGraph();
        else return;
        event.preventDefault();
        this.saveGraphViewport();
      },

      startGraphPan(event) {
        if (event.button !== 0 || event.target.closest('.home-graph-node, .home-graph-tools')) return;
        this.graphPointer = {
          kind: 'pan',
          clientX: event.clientX,
          clientY: event.clientY,
          panX: this.graphPan.x,
          panY: this.graphPan.y,
        };
      },

      startNodeDrag(event, node) {
        if (event.button !== 0 || !node?.id) return;
        const position = this.graphPosition(node.id);
        this.graphPointer = {
          kind: 'node',
          id: node.id,
          clientX: event.clientX,
          clientY: event.clientY,
          x: position.x,
          y: position.y,
          priorPosition: { ...position },
          priorParent: this.parentById[node.id] || node.parent_path || null,
          priorSelection: this.graphSelectedId,
        };
        this.graphDropTargetId = null;
        this.graphInvalidDropTargetId = null;
      },

      moveGraphPointer(event) {
        const pointer = this.graphPointer;
        if (!pointer) return;
        if (pointer.kind === 'pan') {
          this.graphPan = {
            x: pointer.panX + event.clientX - pointer.clientX,
            y: pointer.panY + event.clientY - pointer.clientY,
          };
          return;
        }

        const distance = Math.hypot(event.clientX - pointer.clientX, event.clientY - pointer.clientY);
        if (distance < 5 && !this.graphDraggingId) return;
        this.graphDraggingId = pointer.id;
        this.graphPositions[pointer.id] = {
          x: Math.round(pointer.x + (event.clientX - pointer.clientX) / this.graphZoom),
          y: Math.round(pointer.y + (event.clientY - pointer.clientY) / this.graphZoom),
        };
        this.graphPositions = { ...this.graphPositions };

        const target = document.elementsFromPoint(event.clientX, event.clientY)
          .map((element) => element.closest?.('.home-graph-node[data-graph-topic="true"]'))
          .find((element) => element && element.dataset.graphId !== pointer.id);
        let targetId = target?.dataset.graphId || null;
        if (!targetId && this.nodeById[pointer.id]?.type === 'topic') {
          const canvasBox = this.$refs.graphCanvas?.getBoundingClientRect();
          if (canvasBox) {
            const priorClientX = canvasBox.left + this.graphPan.x + pointer.x * this.graphZoom;
            const priorClientY = canvasBox.top + this.graphPan.y + pointer.y * this.graphZoom;
            const onPriorNode = Math.abs(event.clientX - priorClientX) <= 105 * this.graphZoom
              && Math.abs(event.clientY - priorClientY) <= 34 * this.graphZoom;
            if (onPriorNode) targetId = pointer.id;
          }
        }
        if (!targetId) {
          this.graphDropTargetId = null;
          this.graphInvalidDropTargetId = null;
        } else if (this.validGraphParent(pointer.id, targetId)) {
          this.graphDropTargetId = targetId;
          this.graphInvalidDropTargetId = null;
        } else {
          this.graphDropTargetId = null;
          this.graphInvalidDropTargetId = targetId;
        }
      },

      async endGraphPointer() {
        const pointer = this.graphPointer;
        if (!pointer) return;
        if (pointer.kind === 'pan') {
          this.graphPointer = null;
          this.saveGraphViewport();
          return;
        }
        if (!this.graphDraggingId) {
          this.graphPointer = null;
          return;
        }

        const targetId = this.graphDropTargetId;
        const invalidTargetId = this.graphInvalidDropTargetId;
        this.graphSuppressClick = true;
        this.graphPointer = null;
        this.graphDraggingId = null;
        this.graphDropTargetId = null;
        this.graphInvalidDropTargetId = null;
        window.setTimeout(() => { this.graphSuppressClick = false; }, 0);

        if (targetId) await this.saveGraphParent(pointer, targetId);
        else if (invalidTargetId) {
          this.graphPositions[pointer.id] = pointer.priorPosition;
          this.graphPositions = { ...this.graphPositions };
          this.showGraphNotice(t('home.graph_invalid_parent'), true);
        } else await this.saveGraphPosition(pointer);
      },

      cancelGraphPointer() {
        if (this.graphPointer?.kind === 'node' && this.graphPointer.priorPosition) {
          this.graphPositions[this.graphPointer.id] = this.graphPointer.priorPosition;
          this.graphPositions = { ...this.graphPositions };
        }
        this.graphPointer = null;
        this.graphDraggingId = null;
        this.graphDropTargetId = null;
        this.graphInvalidDropTargetId = null;
      },

      validGraphParent(path, target) {
        if (!path || !target || path === target || this.nodeById[target]?.type !== 'topic') return false;
        if (this.nodeById[target]?.status !== 'active') return false;
        const seen = new Set();
        let current = target;
        while (current && !seen.has(current)) {
          if (current === path) return false;
          seen.add(current);
          current = this.parentById[current];
        }
        return true;
      },

      async saveGraphPosition(pointer) {
        const position = this.graphPosition(pointer.id);
        try {
          await MycelosAPI.put(
            `/api/knowledge/graph/positions/${apiPath(pointer.id)}`,
            { x: position.x, y: position.y }
          );
          this.showGraphNotice(t('home.graph_position_saved'), false);
        } catch (_error) {
          this.graphPositions[pointer.id] = pointer.priorPosition;
          this.graphPositions = { ...this.graphPositions };
          this.showGraphNotice(t('home.graph_position_error'), true);
        }
      },

      async saveGraphParent(pointer, targetId) {
        const priorPosition = pointer.priorPosition;
        const droppedPosition = { ...this.graphPosition(pointer.id) };
        this.graphSelectedId = pointer.id;
        this.applyGraphParent(pointer.id, targetId);
        try {
          await MycelosAPI.put(
            `/api/knowledge/notes/${apiPath(pointer.id)}`,
            { parent_path: targetId }
          );
          this.graphPositions[pointer.id] = droppedPosition;
          this.graphPositions = { ...this.graphPositions };
          this.graphUndo = {
            path: pointer.id,
            previousParent: pointer.priorParent,
            currentParent: targetId,
            position: droppedPosition,
          };
          this.graphKeyboardTargetId = '';
          this.showGraphNotice(t('home.graph_parent_saved'), false);
        } catch (_error) {
          this.applyGraphParent(pointer.id, pointer.priorParent);
          this.graphPositions[pointer.id] = priorPosition;
          this.graphPositions = { ...this.graphPositions };
          this.graphSelectedId = pointer.priorSelection;
          this.graphUndo = null;
          this.showGraphNotice(t('home.graph_parent_error'), true);
        }
      },

      async undoGraphMove() {
        const undo = this.graphUndo;
        if (!undo) return;
        this.applyGraphParent(undo.path, undo.previousParent);
        try {
          await MycelosAPI.put(
            `/api/knowledge/notes/${apiPath(undo.path)}`,
            { parent_path: undo.previousParent }
          );
          this.graphPositions[undo.path] = undo.position;
          this.graphPositions = { ...this.graphPositions };
          this.graphUndo = null;
          this.showGraphNotice(t('home.graph_move_undone'), false);
        } catch (_error) {
          this.applyGraphParent(undo.path, undo.currentParent);
          this.showGraphNotice(t('home.graph_undo_error'), true);
        }
      },

      applyGraphParent(path, parent) {
        if (this.nodeById[path]) this.nodeById[path].parent_path = parent || '';
        const edgeIndex = this.graph.edges.findIndex((edge) => edge?.kind === 'parent' && edge.source === path);
        if (parent) {
          const edge = { source: path, target: parent, kind: 'parent' };
          if (edgeIndex >= 0) this.graph.edges.splice(edgeIndex, 1, edge);
          else this.graph.edges.push(edge);
        } else if (edgeIndex >= 0) this.graph.edges.splice(edgeIndex, 1);
        this.graph.edges = [...this.graph.edges];
        this.buildTreeIndex();
        this.buildGraphChildrenIndex();
      },

      showGraphNotice(message, error) {
        this.graphNotice = { message, error: !!error };
      },

      activateGraphNode(node) {
        if (this.graphSuppressClick || !node?.id) return;
        if (this.graphSelectedId === node.id) this.openGraphNode(node);
        else {
          this.graphSelectedId = node.id;
          this.graphKeyboardTargetId = '';
        }
      },

      selectGraphRelation(relation) {
        if (relation?.id && this.nodeById[relation.id]) {
          this.graphSelectedId = relation.id;
          this.graphKeyboardTargetId = '';
        }
      },

      graphKeyboardTargets() {
        const selected = this.nodeById[this.graphSelectedId];
        if (!selected) return [];
        const currentParent = this.parentById[selected.id] || selected.parent_path || null;
        return this.graphVisibleNodes()
          .filter((node) =>
            node.type === 'topic'
            && node.status === 'active'
            && node.id !== currentParent
            && this.validGraphParent(selected.id, node.id)
          )
          .sort((left, right) =>
            (left.title || left.id).localeCompare(right.title || right.id, undefined, { sensitivity: 'base' })
          );
      },

      async moveSelectedGraphNode() {
        const node = this.nodeById[this.graphSelectedId];
        const targetId = this.graphKeyboardTargetId;
        if (!node || !this.validGraphParent(node.id, targetId)) return;
        const position = { ...this.graphPosition(node.id) };
        await this.saveGraphParent({
          id: node.id,
          priorPosition: position,
          priorParent: this.parentById[node.id] || node.parent_path || null,
          priorSelection: this.graphSelectedId,
        }, targetId);
      },

      handleGraphTargetKeydown(event) {
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
        const targets = this.graphKeyboardTargets();
        if (!targets.length) return;
        const currentIndex = targets.findIndex((target) => target.id === this.graphKeyboardTargetId);
        const direction = event.key === 'ArrowDown' ? 1 : -1;
        const nextIndex = currentIndex < 0
          ? (direction > 0 ? 0 : targets.length - 1)
          : (currentIndex + direction + targets.length) % targets.length;
        event.preventDefault();
        this.graphKeyboardTargetId = targets[nextIndex].id;
      },

      async handleGraphNodeKeydown(event, node) {
        if (event.key === 'Enter') {
          event.preventDefault();
          event.stopPropagation();
          this.openGraphNode(node);
          return;
        }
        const offsets = {
          ArrowLeft: { x: -24, y: 0 },
          ArrowRight: { x: 24, y: 0 },
          ArrowUp: { x: 0, y: -24 },
          ArrowDown: { x: 0, y: 24 },
        };
        const offset = event.altKey ? offsets[event.key] : null;
        if (!offset) return;
        event.preventDefault();
        event.stopPropagation();
        const priorPosition = { ...this.graphPosition(node.id) };
        this.graphPositions[node.id] = {
          x: priorPosition.x + offset.x,
          y: priorPosition.y + offset.y,
        };
        this.graphPositions = { ...this.graphPositions };
        await this.saveGraphPosition({ id: node.id, priorPosition });
      },

      openGraphNode(node) {
        if (!node?.id) return;
        if (node.type === 'topic') {
          window.location.href = `/pages/knowledge.html?topic=${encodeURIComponent(node.id)}`;
        } else window.location.href = this.noteHref(node.id);
      },

      clearGraphSelection() {
        if (!this.graphSelectedId) return false;
        this.graphSelectedId = null;
        this.graphKeyboardTargetId = '';
        return true;
      },
    };
  };
})();
