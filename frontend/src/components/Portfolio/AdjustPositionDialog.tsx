import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { TrendingDown, TrendingUp, X } from "lucide-react";
import type { Holding } from "@/api/client";

interface Props {
  holding: Holding;
  action: "add" | "reduce";
  submitting?: boolean;
  error?: string | null;
  onConfirm: (action: "add" | "reduce", quantity: number, price: number) => void;
  onClose: () => void;
}

function fmt(n: number, digits = 2) {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: digits }).format(n);
}

/** 加仓 / 减仓 弹窗:按一笔交易调整持仓,实时预览新成本(加仓)或已实现盈亏(减仓)。 */
export function AdjustPositionDialog({ holding, action, submitting, error, onConfirm, onClose }: Props) {
  const isAdd = action === "add";
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState(
    holding.current_price != null ? String(holding.current_price) : String(holding.avg_cost),
  );

  const qty = Number(quantity);
  const px = Number(price);
  const qtyValid = Number.isFinite(qty) && qty > 0;
  const pxValid = Number.isFinite(px) && px > 0;
  const overSell = !isAdd && qtyValid && qty > holding.quantity;
  const canSubmit = qtyValid && pxValid && !overSell && !submitting;

  const oldQty = holding.quantity;
  const oldAvg = holding.avg_cost;
  const newQty = canSubmit ? (isAdd ? oldQty + qty : oldQty - qty) : undefined;
  const newAvg = isAdd && newQty !== undefined ? (oldQty * oldAvg + qty * px) / newQty : undefined;
  const realized = !isAdd && canSubmit ? qty * (px - oldAvg) : undefined;
  const closed = !isAdd && newQty !== undefined && newQty <= 0;
  const title = isAdd ? "加仓" : "减仓";

  return (
    <Dialog.Root open onOpenChange={(o) => { if (!o && !submitting) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(92vw,420px)] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-stone-700 bg-stone-900 p-5 shadow-xl">
          <div className="flex items-center justify-between">
            <Dialog.Title className="flex items-center gap-2 text-sm font-semibold text-stone-100">
              {isAdd ? <TrendingUp className="h-4 w-4 text-teal-300" /> : <TrendingDown className="h-4 w-4 text-amber-300" />}
              {title} · {holding.name && holding.name !== holding.symbol ? holding.name : holding.symbol}
            </Dialog.Title>
            <Dialog.Close asChild>
              <button className="rounded p-1 text-stone-500 hover:bg-stone-800 hover:text-stone-200" disabled={submitting} aria-label="关闭">
                <X className="h-4 w-4" />
              </button>
            </Dialog.Close>
          </div>

          <div className="mt-2 text-xs text-stone-500">
            当前持仓 <span className="font-mono text-stone-300">{fmt(oldQty)}</span> 股 · 成本 <span className="font-mono text-stone-300">{fmt(oldAvg)}</span>
          </div>

          <div className="mt-4 grid grid-cols-2 gap-3">
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-stone-400">{isAdd ? "买入数量" : "卖出数量"}</span>
              <input
                type="number"
                min={0}
                step="any"
                value={quantity}
                placeholder="100"
                onChange={(e) => setQuantity(e.target.value)}
                className="w-full rounded-lg border border-stone-700 bg-stone-950 px-3 py-2 text-sm text-stone-100 outline-none focus:border-teal-400"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-stone-400">{isAdd ? "买入价" : "卖出价"}</span>
              <input
                type="number"
                min={0}
                step="any"
                value={price}
                placeholder="0.00"
                onChange={(e) => setPrice(e.target.value)}
                className="w-full rounded-lg border border-stone-700 bg-stone-950 px-3 py-2 text-sm text-stone-100 outline-none focus:border-teal-400"
              />
            </label>
          </div>

          {overSell && (
            <div className="mt-3 rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
              卖出数量超过当前持仓({fmt(oldQty)} 股)。
            </div>
          )}
          {error && (
            <div className="mt-3 rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
              {error}
            </div>
          )}

          {newQty !== undefined && (
            <div className="mt-3 rounded-lg border border-stone-700 bg-stone-950 px-3 py-2 text-xs text-stone-300">
              {isAdd ? (
                <div>
                  加仓后 <span className="font-mono text-stone-100">{fmt(newQty)}</span> 股 · 新成本{" "}
                  <span className="font-mono text-teal-300">{fmt(newAvg!)}</span>
                </div>
              ) : (
                <div>
                  减仓后 <span className="font-mono text-stone-100">{fmt(newQty)}</span> 股
                  {closed && <span className="ml-2 text-amber-300">(清仓)</span>}
                  {!closed && realized !== undefined && (
                    <span className={`ml-2 font-mono ${realized >= 0 ? "text-emerald-300" : "text-red-300"}`}>
                      已实现 {realized >= 0 ? "+" : ""}{fmt(realized)}
                    </span>
                  )}
                </div>
              )}
            </div>
          )}

          <div className="mt-5 flex justify-end gap-2">
            <button
              onClick={onClose}
              disabled={submitting}
              className="rounded-lg border border-stone-700 px-3 py-2 text-sm text-stone-300 transition hover:bg-stone-800 disabled:opacity-50"
            >
              取消
            </button>
            <button
              onClick={() => canSubmit && onConfirm(action, qty, px)}
              disabled={!canSubmit}
              className={`inline-flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-semibold text-stone-950 transition disabled:cursor-not-allowed disabled:opacity-50 ${
                isAdd ? "bg-teal-500 hover:bg-teal-400" : "bg-amber-400 hover:bg-amber-300"
              }`}
            >
              {submitting ? "提交中..." : `确认${title}`}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
