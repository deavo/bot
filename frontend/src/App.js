import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import "./App.css";
import { ChevronLeft, ChevronRight, RefreshCcw } from "lucide-react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

const DEFAULT_CATEGORIES = ["Любовь", "Жизнь", "Мотивация", "Дружба", "Юмор"];

function useApi() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const call = async (method, url, data) => {
    setLoading(true);
    setError(null);
    try {
      const res = await axios({ method, url, data });
      return res.data;
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
      throw e;
    } finally {
      setLoading(false);
    }
  };
  return { loading, error, call };
}

function Header({ onImport, importing }) {
  return (
    <div className="header container">
      <a className="brand" href="https://emergent.sh" target="_blank" rel="noreferrer">
        <img src="https://avatars.githubusercontent.com/in/1201222?s=120&u=2686cf91179bbafbc7a71bfbc43004cf9ae1acea&v=4" alt="logo" />
        <h1>Quotify — цитаты по категориям</h1>
      </a>
      <button className="button" onClick={onImport} disabled={importing}>
        {importing ? "Импорт..." : "Загрузить демо цитаты"}
      </button>
    </div>
  );
}

function Tabs({ categories, active, onChange }) {
  return (
    <div className="tabs">
      {categories.map((c) => (
        <button
          key={c}
          className={`tab ${active === c ? "active" : ""}`}
          onClick={() => onChange(c)}
        >
          {c}
        </button>
      ))}
    </div>
  );
}

function QuoteCard({ quote, onPrev, onNext, disablePrev, disableNext }) {
  return (
    <div className="quoteCard">
      <button className="arrowBtn arrowLeft" onClick={onPrev} disabled={disablePrev} aria-label="Предыдущая">
        <ChevronLeft size={20} />
      </button>
      <button className="arrowBtn arrowRight" onClick={onNext} disabled={disableNext} aria-label="Следующая">
        <ChevronRight size={20} />
      </button>
      <div className="quoteText">“{quote?.text || "Нет цитат"}”</div>
      {quote && (
        <div className="quoteMeta">— {quote.author || "Неизвестный"} · Категория: {quote.category}</div>
      )}
    </div>
  );
}

export default function App() {
  const { loading, error, call } = useApi();
  const [categories, setCategories] = useState(DEFAULT_CATEGORIES);
  const [active, setActive] = useState(DEFAULT_CATEGORIES[0]);
  const [pageByCategory, setPageByCategory] = useState({});
  const [quotesByCategory, setQuotesByCategory] = useState({});

  const activeQuotes = quotesByCategory[active] || [];
  const activePage = pageByCategory[active] || 1;
  const [indexByCategory, setIndexByCategory] = useState({});
  const activeIndex = indexByCategory[active] || 0;

  const disablePrev = activeIndex <= 0 && activePage <= 1;
  const disableNext = activeQuotes.length === 0;

  const fetchCategories = async () => {
    try {
      const data = await call("GET", `${API}/quotes/categories`);
      const serverCats = (data.categories || []).map((c) => c.name);
      if (serverCats.length > 0) setCategories(serverCats);
    } catch (_) {}
  };

  const fetchQuotes = async (category, page = 1) => {
    const data = await call("GET", `${API}/quotes`, { params: { category, page, limit: 20 } });
    // axios config above doesn't pass params when using data with GET, so adjust:
    // Re-call correctly
    const res = await axios.get(`${API}/quotes`, { params: { category, page, limit: 20 } });
    const payload = res.data;
    setQuotesByCategory((prev) => ({ ...prev, [category]: payload.items }));
    setPageByCategory((prev) => ({ ...prev, [category]: payload.page }));
    setIndexByCategory((prev) => ({ ...prev, [category]: 0 }));
  };

  const importDemo = async () => {
    try {
      await call("POST", `${API}/quotes/import`);
      await fetchCategories();
      await fetchQuotes(active, 1);
    } catch (_) {}
  };

  const next = async () => {
    const list = quotesByCategory[active] || [];
    const idx = activeIndex + 1;
    if (idx < list.length) {
      setIndexByCategory((p) => ({ ...p, [active]: idx }));
    } else {
      // try next page
      const nextPage = (pageByCategory[active] || 1) + 1;
      await fetchQuotes(active, nextPage);
    }
  };

  const prev = () => {
    const idx = activeIndex - 1;
    if (idx >= 0) {
      setIndexByCategory((p) => ({ ...p, [active]: idx }));
    }
  };

  useEffect(() => {
    // initial load
    (async () => {
      await fetchCategories();
      await fetchQuotes(active, 1);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // whenever active category changes, load page 1
    (async () => {
      await fetchQuotes(active, 1);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  const currentQuote = activeQuotes[activeIndex];

  return (
    <div className="App">
      <Header onImport={importDemo} importing={loading} />

      <div className="container">
        <Tabs categories={categories} active={active} onChange={setActive} />

        <QuoteCard
          quote={currentQuote}
          onPrev={prev}
          onNext={next}
          disablePrev={disablePrev}
          disableNext={disableNext}
        />

        {error && (
          <div className="actions">
            <button className="button secondary" onClick={() => fetchQuotes(active, 1)}>
              <RefreshCcw size={16} style={{ marginRight: 8 }} /> Повторить
            </button>
            <div style={{ color: "#fca5a5" }}>{error}</div>
          </div>
        )}

        <div className="footer">
          Бэкенд: переменная REACT_APP_BACKEND_URL, все API идут на /api. Стрелки — навигация, по 5+ цитат в каждой категории.
        </div>
      </div>
    </div>
  );
}