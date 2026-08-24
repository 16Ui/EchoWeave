from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SessionTreeNode:
    session_id: str
    parent_id: str | None = None
    branch_label: str | None = None
    path: Path | None = None
    children: list["SessionTreeNode"] = field(default_factory=list)


@dataclass
class SessionTree:
    """会话谱系树：通过 parent_id 维护分叉后的父子关系。"""

    roots: list[SessionTreeNode] = field(default_factory=list)

    @property
    def root(self) -> SessionTreeNode | None:
        return self.roots[0] if self.roots else None

    def attach(self, node: SessionTreeNode) -> None:
        """把节点挂到父节点下；若父不存在则作为根节点。"""
        parent = self.find(node.parent_id) if node.parent_id else None
        if parent is None:
            self.roots.append(node)
            return
        parent.children.append(node)

    def find(self, session_id: str | None) -> SessionTreeNode | None:
        """按 session_id 在整棵树里查找节点（深度优先）。"""
        if session_id is None:
            return None
        for root in self.roots:
            found = self._find(root, session_id)
            if found is not None:
                return found
        return None

    def _find(self, node: SessionTreeNode, session_id: str) -> SessionTreeNode | None:
        """递归 DFS：先看当前节点，再遍历子节点。"""
        if node.session_id == session_id:
            return node
        for child in node.children:
            found = self._find(child, session_id)
            if found is not None:
                return found
        return None
