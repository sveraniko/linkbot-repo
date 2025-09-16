# SPEC v2 Requirements Implementation Mapping

This document maps each requirement from the SPEC v2 specification to its implementation in the codebase.

## Section 1: Current Problems (What Was Fixed)

| Problem | Location | Solution |
|---------|----------|----------|
| Search not working for all three types (id/tag/name) | `app/handlers/ask.py` | Enhanced `_parse_search_query()` function to properly handle all three input types |
| LLM calls in search/selection steps (prohibited) | Multiple files | Verified no LLM calls in search flows; ensured catch-all respects `awaiting_ask_search` flag |
| Memory-List: One artifact becomes 3-4 rows | `app/handlers/memory_panel.py` | Changed to "плашка + 1 строка кнопок" pattern |
| Buttons go in a column; toggle `➕/🧺` only changes after "Refresh" | Multiple handler files | Implemented instant toggle with `edit_message_reply_markup` |
| No pagination - all items shown at once | Multiple handler files | Added pagination with 5 items per page and proper navigation |

## Section 2: UX Invariants Implementation

| UX Invariant | Implementation Location | Details |
|-------------|------------------------|---------|
| Each artifact = one text plaque (not interactive button) | `memory_panel.py`, `ask.py` → `_render_panel()` | Text format: `N. <Название> [#tag1 #tag2] (id 689..., дата)` |
| Under plaque - 1 row of 2-3 buttons: `➕/🧺`, `🗑` | `memory_panel.py`, `ask.py` | Inline keyboard with exactly 2 buttons per artifact |
| Pagination: 5 items → `⬅️ Назад • Стр. X/Y • Далее ➡️` | `memory_panel.py`, `ask.py` | Footer message with proper navigation controls |
| Instant toggle: `➕` instantly becomes `🧺` (and vice versa), no "Refresh" | `memory_toggle()`, `ask_toggle()` | Using `edit_message_reply_markup` for instant feedback |
| No LLM calls in search/selection - works with Chat: OFF | All search/selection handlers | Verified no LLM calls in these flows |

## Section 3: Implementation Locations

### 3.1 `app/handlers/memory_panel.py`
**Changes:**
- `memory_list()` handler: 
  - Changed page size from 10 to 5 items
  - Implemented "плашка + 1 строка кнопок" pattern
  - Added pagination with footer message
  - Implemented message cleanup for smooth transitions
- `memory_toggle()` handler:
  - Added instant toggle functionality
  - Used `edit_message_reply_markup` for immediate visual feedback

### 3.2 `app/handlers/ask.py`
**Changes:**
- `_render_panel()` function:
  - Made identical to Memory-List pattern
  - Implemented 5 items per page pagination
  - Added proper message management
- `ask_toggle()` handler:
  - Added instant toggle functionality
  - Used `edit_message_reply_markup` for immediate visual feedback
- Search handlers (`ask_search`, `ask_search_reply`):
  - Enhanced search parsing logic
  - Proper `awaiting_ask_search` flag handling

### 3.3 `app/services/memory.py`
**Changes:**
- `list_artifacts()` function:
  - Implemented SPEC v2 "Вариант B" SQL approach
  - Subquery with `distinct artifact.id` and all filters
  - Main query using `where(Artifact.id.in_(subq))`

### 3.4 `app/models.py`
**Changes:**
- `UserState` model:
  - Added `memory_page_msg_ids` field
  - Added `memory_footer_msg_id` field
  - Added `ask_page_msg_ids` field
  - Added `ask_footer_msg_id` field

### 3.5 Migration Files
**Changes:**
- `alembic/versions/0013_add_memory_pagination_fields.py`:
  - Migration for Memory panel pagination fields
- `alembic/versions/0014_add_ask_pagination_fields.py`:
  - Migration for ASK panel pagination fields

## Section 4: Search Implementation

| Search Type | Implementation | Location |
|-------------|---------------|----------|
| ID: Numeric input → exact `Artifact.id` search | `_parse_search_query()` | `app/handlers/ask.py` |
| Tag: `#` prefix → `lower(tag.name) LIKE %term%` | `_parse_search_query()` | `app/handlers/ask.py` |
| Name: Other input → `lower(artifact.title) LIKE %term%` | `_parse_search_query()` | `app/handlers/ask.py` |
| Result display | `_render_panel()` | `app/handlers/ask.py` |

## Section 5: SQL Implementation (Variant B - Required)

**Implementation:** `app/services/memory.py` → `list_artifacts()`

**Approach:**
1. Subquery `subq`: `select(Artifact.id)` + all filters + `.distinct()`
2. For tag filtering: `join` within subquery
3. Main query: `select(Artifact).where(Artifact.id.in_(subq)).order_by(Artifact.created_at.desc()).limit/offset`
4. Additional deduplication in interface (insurance)

## Section 6: Message Management

| Feature | Implementation | Location |
|---------|---------------|----------|
| Plaques: Each plaque - separate message with own inline keyboard | `_render_panel()` | `memory_panel.py`, `ask.py` |
| Pagination: Delete current page messages and draw new ones | `memory_list()`, `_render_panel()` | `memory_panel.py`, `ask.py` |
| Toggle: Edit only the keyboard of the clicked message | `memory_toggle()`, `ask_toggle()` | `memory_panel.py`, `ask.py` |
| Delete (`🗑`): Delete plaque and redraw page or show "Deleted" | `memory_delete()`, `ask_delete()` | `memory_panel.py`, `ask.py` |

## Section 7: LLM Stop (Mandatory Requirement)

| Requirement | Implementation | Location |
|-------------|---------------|----------|
| No LLM calls in search/selection/pagination/toggle/delete steps | Verified all handlers | Multiple files |
| If project has `LLM_DISABLED` flag - should be enabled during work | Maintained existing configuration | `app/llm.py` |
| Catch-all must be silent when `awaiting_ask_search=True` | Enhanced chat handler | `app/handlers/chat.py` |

## Section 8: Acceptance Criteria Verification

| Criteria | Status | Implementation Location |
|---------|--------|------------------------|
| 1. Memory → List: 7-10 documents show 5 plaques; bottom footer `⬅️/➡️`; no duplicates | ✅ | `memory_panel.py` |
| 2. Click `➕` on any plaque → icon changes to `🧺` instantly; repeat click → back | ✅ | `memory_toggle()`, `ask_toggle()` |
| 3. Click `🗑` → document deleted without "clutter"; page remains correct | ✅ | `memory_delete()`, `ask_delete()` |
| 4. ASK → 🔍: `women` → list; `#woman` → list; `id` → one plaque. No SQL crashes, no LLM attempts | ✅ | `ask.py` search handlers |
| 5. Pagination "Next/Back" - always 5 on screen; old 5 deleted | ✅ | `memory_list()`, `_render_panel()` |
| 6. Bottom Reply-Keyboard always visible; user responses (ForceReply) deleted | ✅ | All handlers with `main_reply_kb()` |

## Section 9: Output Delivery

**Updated Files:**
1. `app/handlers/memory_panel.py` - Memory list rendering and pagination
2. `app/handlers/ask.py` - ASK wizard rendering and search
3. `app/services/memory.py` - Artifact listing with proper SQL
4. `app/models.py` - UserState model with new fields
5. `alembic/versions/0013_add_memory_pagination_fields.py` - Migration for Memory fields
6. `alembic/versions/0014_add_ask_pagination_fields.py` - Migration for ASK fields

**Documentation:**
- `SPEC_V2_IMPLEMENTATION_SUMMARY.md` - Implementation details
- `SPEC_V2_REQUIREMENTS_MAPPING.md` - This document (requirements mapping)

## Section 10: Icon Usage

| Icon | Usage | Implementation |
|------|-------|----------------|
| `➕` | Not selected toggle | `memory_toggle()`, `ask_toggle()` |
| `🧺` | Selected toggle | `memory_toggle()`, `ask_toggle()` |
| `🗑` | Delete artifact | All delete handlers |
| `⬅️` | Pagination back | Pagination handlers |
| `➡️` | Pagination next | Pagination handlers |
| `↩️` | Back to menu (optional) | Not implemented as per default behavior |

## Summary

All SPEC v2 requirements have been successfully implemented with attention to the exact specifications provided. The implementation follows the staged approach with clear separation of concerns and maintains consistency between Memory and ASK interfaces.