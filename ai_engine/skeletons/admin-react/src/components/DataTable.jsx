import { useState, useMemo } from 'react';
import { ChevronUp, ChevronDown, Search, ChevronLeft, ChevronRight } from 'lucide-react';

/**
 * Reusable data table with sorting, search, and pagination.
 *
 * @param {object} props
 * @param {Array} props.data — array of row objects
 * @param {Array} props.columns — [{ key, label, render? }]
 * @param {number} [props.pageSize=10]
 * @param {string} [props.searchKey] — key to search by (defaults to first column)
 * @param {Function} [props.onRowClick] — callback(row)
 * @param {React.ReactNode} [props.actions] — header actions (e.g. Add button)
 * @param {string} [props.title]
 */
export default function DataTable({
  data = [],
  columns = [],
  pageSize = 10,
  searchKey,
  onRowClick,
  actions,
  title,
}) {
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState(null);
  const [sortDir, setSortDir] = useState('asc');
  const [page, setPage] = useState(0);

  const effectiveSearchKey = searchKey || columns[0]?.key;

  const filtered = useMemo(() => {
    if (!search.trim()) return data;
    const q = search.toLowerCase();
    return data.filter((row) => {
      const val = row[effectiveSearchKey];
      return val != null && String(val).toLowerCase().includes(q);
    });
  }, [data, search, effectiveSearchKey]);

  const sorted = useMemo(() => {
    if (!sortKey) return filtered;
    return [...filtered].sort((a, b) => {
      const va = a[sortKey] ?? '';
      const vb = b[sortKey] ?? '';
      if (va < vb) return sortDir === 'asc' ? -1 : 1;
      if (va > vb) return sortDir === 'asc' ? 1 : -1;
      return 0;
    });
  }, [filtered, sortKey, sortDir]);

  const totalPages = Math.ceil(sorted.length / pageSize);
  const paged = sorted.slice(page * pageSize, (page + 1) * pageSize);

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  };

  return (
    <div className="data-table-wrapper">
      {/* Header */}
      <div className="data-table-header">
        <div className="data-table-header-left">
          {title && <h2 className="data-table-title">{title}</h2>}
          <span className="data-table-count">{sorted.length} items</span>
        </div>
        <div className="data-table-header-right">
          <div className="data-table-search">
            <Search size={16} />
            <input
              type="text"
              placeholder="Search..."
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(0); }}
            />
          </div>
          {actions}
        </div>
      </div>

      {/* Table */}
      <div className="data-table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              {columns.map((col) => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  className="sortable"
                >
                  <span>{col.label}</span>
                  {sortKey === col.key && (
                    sortDir === 'asc' ? <ChevronUp size={14} /> : <ChevronDown size={14} />
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {paged.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="data-table-empty">
                  No data found
                </td>
              </tr>
            ) : (
              paged.map((row, i) => (
                <tr
                  key={row.id || i}
                  onClick={() => onRowClick?.(row)}
                  className={onRowClick ? 'clickable' : ''}
                >
                  {columns.map((col) => (
                    <td key={col.key}>
                      {col.render ? col.render(row[col.key], row) : row[col.key]}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="data-table-pagination">
          <button
            disabled={page === 0}
            onClick={() => setPage(page - 1)}
          >
            <ChevronLeft size={16} />
          </button>
          <span>{page + 1} / {totalPages}</span>
          <button
            disabled={page >= totalPages - 1}
            onClick={() => setPage(page + 1)}
          >
            <ChevronRight size={16} />
          </button>
        </div>
      )}
    </div>
  );
}
