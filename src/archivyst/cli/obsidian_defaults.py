"""
Default Obsidian vault configuration data.

These constants define the minimal Obsidian vault structure
that Archivyst creates when initializing a new project.
"""

APP_JSON: dict[str, object] = {}

APPEARANCE_JSON: dict[str, object] = {}

CORE_PLUGINS_JSON: dict[str, object] = {
    "file-explorer": True,
    "global-search": True,
    "switcher": True,
    "graph": True,
    "backlink": True,
    "canvas": True,
    "outgoing-link": True,
    "tag-pane": True,
    "footnotes": False,
    "properties": True,
    "page-preview": True,
    "daily-notes": True,
    "templates": True,
    "note-composer": True,
    "command-palette": True,
    "slash-command": False,
    "editor-status": True,
    "bookmarks": True,
    "markdown-importer": False,
    "zk-prefixer": False,
    "random-note": False,
    "outline": True,
    "word-count": True,
    "slides": False,
    "audio-recorder": False,
    "workspaces": False,
    "file-recovery": True,
    "publish": False,
    "sync": True,
    "bases": True,
    "webviewer": False,
}

GRAPH_JSON: dict[str, object] = {
    "collapse-filter": True,
    "search": "",
    "showTags": False,
    "showAttachments": False,
    "hideUnresolved": False,
    "showOrphans": True,
    "collapse-color-groups": True,
    "colorGroups": [],
    "collapse-display": True,
    "showArrow": False,
    "textFadeMultiplier": 0,
    "nodeSizeMultiplier": 1,
    "lineSizeMultiplier": 1,
    "collapse-forces": True,
    "centerStrength": 0.518713248970312,
    "repelStrength": 10,
    "linkStrength": 1,
    "linkDistance": 250,
    "scale": 1,
    "close": True,
}

WORKSPACE_JSON: dict[str, object] = {
    "main": {
        "id": "main",
        "type": "split",
        "children": [
            {
                "id": "editor-tabs",
                "type": "tabs",
                "children": [
                    {
                        "id": "welcome-leaf",
                        "type": "leaf",
                        "state": {
                            "type": "markdown",
                            "state": {
                                "file": "Welcome.md",
                                "mode": "source",
                                "source": False,
                            },
                            "icon": "lucide-file",
                            "title": "Welcome",
                        },
                    }
                ],
            },
            {
                "id": "graph-tabs",
                "type": "tabs",
                "children": [
                    {
                        "id": "graph-leaf",
                        "type": "leaf",
                        "state": {
                            "type": "graph",
                            "state": {},
                            "icon": "lucide-git-fork",
                            "title": "Graph view",
                        },
                    }
                ],
            },
        ],
        "direction": "vertical",
    },
    "left": {
        "id": "left",
        "type": "split",
        "children": [
            {
                "id": "left-tabs",
                "type": "tabs",
                "children": [
                    {
                        "id": "file-explorer-leaf",
                        "type": "leaf",
                        "state": {
                            "type": "file-explorer",
                            "state": {
                                "sortOrder": "alphabetical",
                                "autoReveal": False,
                            },
                            "icon": "lucide-folder-closed",
                            "title": "Files",
                        },
                    },
                    {
                        "id": "search-leaf",
                        "type": "leaf",
                        "state": {
                            "type": "search",
                            "state": {
                                "query": "",
                                "matchingCase": False,
                                "explainSearch": False,
                                "collapseAll": False,
                                "extraContext": False,
                                "sortOrder": "alphabetical",
                            },
                            "icon": "lucide-search",
                            "title": "Search",
                        },
                    },
                    {
                        "id": "bookmarks-leaf",
                        "type": "leaf",
                        "state": {
                            "type": "bookmarks",
                            "state": {},
                            "icon": "lucide-bookmark",
                            "title": "Bookmarks",
                        },
                    },
                ],
            }
        ],
        "direction": "horizontal",
        "width": 300,
    },
    "right": {
        "id": "right",
        "type": "split",
        "children": [
            {
                "id": "right-tabs",
                "type": "tabs",
                "children": [
                    {
                        "id": "backlink-leaf",
                        "type": "leaf",
                        "state": {
                            "type": "backlink",
                            "state": {
                                "file": "Welcome.md",
                                "collapseAll": False,
                                "extraContext": False,
                                "sortOrder": "alphabetical",
                                "showSearch": False,
                                "searchQuery": "",
                                "backlinkCollapsed": False,
                                "unlinkedCollapsed": True,
                            },
                            "icon": "links-coming-in",
                            "title": "Backlinks",
                        },
                    },
                    {
                        "id": "outgoing-link-leaf",
                        "type": "leaf",
                        "state": {
                            "type": "outgoing-link",
                            "state": {
                                "file": "Welcome.md",
                                "linksCollapsed": False,
                                "unlinkedCollapsed": True,
                            },
                            "icon": "links-going-out",
                            "title": "Outgoing links",
                        },
                    },
                    {
                        "id": "tag-leaf",
                        "type": "leaf",
                        "state": {
                            "type": "tag",
                            "state": {
                                "sortOrder": "frequency",
                                "useHierarchy": True,
                                "showSearch": False,
                                "searchQuery": "",
                            },
                            "icon": "lucide-tags",
                            "title": "Tags",
                        },
                    },
                    {
                        "id": "outline-leaf",
                        "type": "leaf",
                        "state": {
                            "type": "outline",
                            "state": {
                                "file": "Welcome.md",
                                "followCursor": False,
                                "showSearch": False,
                                "searchQuery": "",
                            },
                            "icon": "lucide-list",
                            "title": "Outline",
                        },
                    },
                ],
            }
        ],
        "direction": "horizontal",
        "width": 300,
        "collapsed": True,
    },
    "left-ribbon": {
        "hiddenItems": {
            "switcher:Open quick switcher": False,
            "graph:Open graph view": False,
            "canvas:Create new canvas": False,
            "daily-notes:Open today's daily note": False,
            "templates:Insert template": False,
            "command-palette:Open command palette": False,
        }
    },
    "active": "welcome-leaf",
    "lastOpenFiles": ["Welcome.md"],
}
