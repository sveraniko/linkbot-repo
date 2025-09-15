# ProjectMemoryBot Fixes Summary

## Issues Fixed

### 1. Search Functionality Crash
**Problem**: `InvalidColumnReferenceError: SELECT DISTINCT ON expressions must match initial ORDER BY expressions`

**Solution**: 
- Replaced `DISTINCT ON` with subquery approach as recommended
- Implemented proper search query parsing:
  - Numeric input → ID search
  - # prefix → tag search  
  - Otherwise → title search

**Files Modified**: 
- `app/handlers/ask.py`

### 2. Duplicate Artifacts in Source List
**Problem**: One artifact shown 4 times (one row per action: 🗑, 🏷, ✏️, 🔎)

**Solution**:
- Show one row per artifact with [➕/🧺, 🗑] buttons only
- Toggle button changes icon based on selection state (➕/🧺)
- Added delete functionality with `aw:delete` callback handler

**Files Modified**: 
- `app/handlers/ask.py`

### 3. Global LLM Stop Switches
**Problem**: LLM was being called even when disabled

**Solution**:
- Verified `LLM_DISABLED = os.getenv("LLM_DISABLED", "1") == "1"` defaults to disabled for tests
- Ensured LLM is only called from single point after ASK arm

**Files Modified**: 
- `app/llm.py`

### 4. ASK Wizard Implementation
**Problem**: ASK became a non-wizard with improper workflow

**Solution**:
- Implemented proper namespace separation using `aw:` prefix
- Fixed wizard workflow with proper state management
- Ensured no LLM calls during wizard steps

**Files Modified**: 
- `app/handlers/ask.py`
- `app/handlers/menu.py`

### 5. Import Last Functionality
**Problem**: Import last button errors due to fragile file download chain

**Solution**:
- Used direct file download by ID instead of get_file->download_file chain
- Removed data mutation in callback handlers
- Created unified import handler

**Files Modified**: 
- `app/handlers/menu.py`
- `app/handlers/ask.py`

### 6. Memory List Duplicates
**Problem**: Duplicates when filtering by tags

**Solution**:
- Added `distinct(Artifact.id)` to tag filtering query

**Files Modified**: 
- `app/services/memory.py`

### 7. Service Text Filtering
**Problem**: Service texts entering free chat

**Solution**:
- Implemented proper filtering in catch-all handler
- Ensured proper router registration order

**Files Modified**: 
- `app/handlers/__init__.py`
- `app/handlers/chat.py`

### 8. Actions Button and Quick Actions Panel
**Problem**: Missing menu items from quick actions

**Solution**:
- Ensured proper handler registration order
- Restored missing menu items (Sources, Quiet, Projects)

**Files Modified**: 
- `app/handlers/__init__.py`

## Testing Instructions

To verify all fixes work correctly:

1. **Search Testing**:
   - Enter "women" → Should show list without crashes
   - Enter #woman → Should show list without crashes  
   - Enter exact artifact ID → Should return exactly one row

2. **Duplicate Verification**:
   - Check that no artifact appears multiple times in lists
   - Each artifact should have only two buttons [➕/🧺, 🗑]

3. **Toggle Functionality**:
   - Toggle ➕/🧺 should reflect selection state
   - Should not trigger LLM

4. **Delete Functionality**:
   - 🗑 button should delete artifacts
   - Should not break pagination/panel

## Files Modified Summary

- `app/handlers/ask.py` - Major fixes for search, duplicates, toggle buttons
- `app/handlers/menu.py` - Import last functionality fixes
- `app/services/memory.py` - Memory list deduplication
- `app/llm.py` - LLM disable configuration
- `app/handlers/__init__.py` - Router registration order
- `app/models.py` - Added awaiting_ask_search field
- `alembic/versions/0012_add_awaiting_ask_search.py` - Migration for new field