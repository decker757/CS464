/**
 * Turns each market's list of trades into rounds: round n holds the nth trade
 * of every market that has one, in market order. The seed pauses between
 * rounds, so every market's trades land minutes apart rather than all at once,
 * and its price history has a shape (#454).
 */
export function inRounds<Trade>(tradesByMarket: Trade[][]): Trade[][] {
  const rounds: Trade[][] = []
  for (const trades of tradesByMarket) {
    trades.forEach((trade, index) => {
      if (!rounds[index]) rounds[index] = []
      rounds[index].push(trade)
    })
  }
  return rounds
}
