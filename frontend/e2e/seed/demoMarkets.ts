// The demo database's content (#454): who trades, what they trade, and where
// each market ends up. Every market is priced at b = 100 (the e2e helpers pin
// it), so the trades below are sized to move prices well apart: the open
// Yes/No markets end near 13%, 33%, 47%, 61%, 75% and 90% Yes, and the
// five-way market near 35 / 21 / 16 / 14 / 14. No trader spends more than
// about 185 credits, so any STARTING_CREDITS from 200 up covers them.
//
// Every open market closes three weeks or more after seeding, so a seed run a
// week or two before the showcase is still open on the day. Names are SMU's
// where they are certain, and generic ("the campus green") where they are not.
//
// The full browse list puts the markets still trading first, soonest close
// first (market_service's `_browse_order`). So the four `headline` markets, the
// Yes/No ones that end furthest from 50/50 and the five-way one, close before
// every other open market, and lead the list below the featured market and
// its bands (#448).

export const ADMINS = { creator: 'admin_rachel', approver: 'admin_kumar' }

// `nur_h` places the resting limit orders below and trades nothing else, so
// what those orders hold is everything that account has committed.
export const ORDER_TRADER = 'nur_h'

export const TRADERS = ['wei_ling', 'arjun_k', 'marcus_tan', 'priya_s', 'jun_hao', 'sarah_lim', 'daniel_ng', 'aisyah_r', ORDER_TRADER]

// Suspended once the seed has finished, so the admin users list has an
// account to show suspended, with its reason ([4.2] #14).
export const SUSPENDED = {
  trader: 'aisyah_r',
  reason: 'Two students were sharing this account, against the one-person-per-account rule.',
}

export interface DemoTrade {
  trader: string
  outcome: string
  quantity: string
}

/**
 * A limit buy placed after the trading rounds ([T-6] #26). Its limit sits a
 * few points under the outcome's seeded price, so it rests when placed, and a
 * modest trade on the other side during the demo pulls the price down to it
 * and fills it inside that trade (ADR 0024).
 */
export interface DemoOrder {
  outcome: string
  limitPrice: string
  quantity: string
}

interface DemoMarketBase {
  question: string
  outcomes: string[]
  closesInDays: number
  resolutionCriteria: string
  resolutionSource: { url: string; label: string }
  trades: DemoTrade[]
}

/** Where the seed leaves a market, and what it needs to get it there. */
export type DemoMarket = DemoMarketBase &
  (
    | { endsAs: 'open'; headline?: true; restingOrder?: DemoOrder }
    | { endsAs: 'closed'; closeReason: string }
    | { endsAs: 'voided'; closeReason: string; voidReason: string }
    | { endsAs: 'pending_resolution' | 'approved' | 'settled'; closeReason: string; winner: string; evidenceNote: string }
  )

const YES_NO = ['Yes', 'No']

function trade(trader: string, outcome: string, quantity: number): DemoTrade {
  return { trader, outcome, quantity: String(quantity) }
}

export const DEMO_MARKETS: DemoMarket[] = [
  {
    question: "Will Round 1 bidding for IS111 Introduction to Programming close above 40 e$ in next term's BOSS?",
    outcomes: YES_NO,
    closesInDays: 30,
    resolutionCriteria:
      'Resolves YES if the minimum successful bid for any IS111 section in Round 1 of BOSS bidding, as published on the bidding results page, is above 40 e$.',
    resolutionSource: { url: 'https://www.smu.edu.sg', label: 'SMU BOSS bidding results' },
    trades: [
      trade('wei_ling', 'Yes', 40),
      trade('arjun_k', 'Yes', 30),
      trade('marcus_tan', 'No', 25),
      trade('priya_s', 'Yes', 45),
      trade('jun_hao', 'No', 20),
      trade('sarah_lim', 'Yes', 35),
      trade('daniel_ng', 'Yes', 15),
      trade('aisyah_r', 'No', 10),
    ],
    endsAs: 'open',
    headline: true,
    restingOrder: { outcome: 'No', limitPrice: '0.2000', quantity: '50' },
  },
  {
    question: "Will more than half of SMU's undergraduates vote in next year's student council election?",
    outcomes: YES_NO,
    closesInDays: 24,
    resolutionCriteria:
      'Resolves YES if the turnout the student council publishes for its next general election is above 50% of eligible undergraduates.',
    resolutionSource: { url: 'https://www.smu.edu.sg', label: 'SMU student council' },
    trades: [
      trade('marcus_tan', 'No', 60),
      trade('wei_ling', 'Yes', 20),
      trade('daniel_ng', 'No', 70),
      trade('aisyah_r', 'No', 40),
      trade('arjun_k', 'Yes', 15),
      trade('jun_hao', 'No', 55),
    ],
    endsAs: 'open',
    headline: true,
  },
  {
    question: "Will SMU's team reach the international rounds of the 2027 Jessup Moot Court Competition?",
    outcomes: YES_NO,
    closesInDays: 60,
    resolutionCriteria:
      'Resolves YES if an SMU team is listed among the teams advancing to the international rounds of the 2027 Philip C. Jessup International Law Moot Court Competition.',
    resolutionSource: { url: 'https://www.ilsa.org/jessup', label: 'ILSA Jessup competition' },
    trades: [
      trade('priya_s', 'Yes', 30),
      trade('sarah_lim', 'No', 45),
      trade('arjun_k', 'No', 35),
      trade('wei_ling', 'Yes', 10),
      trade('daniel_ng', 'No', 30),
    ],
    endsAs: 'open',
  },
  {
    question: 'Will Li Ka Shing Library stay open 24 hours a day through the whole Term 1 final exam period?',
    outcomes: YES_NO,
    closesInDays: 21,
    resolutionCriteria:
      'Resolves YES if the SMU Libraries opening hours show Li Ka Shing Library open around the clock on every day of the Term 1 final exam period.',
    resolutionSource: { url: 'https://library.smu.edu.sg', label: 'SMU Libraries opening hours' },
    trades: [
      trade('jun_hao', 'Yes', 50),
      trade('aisyah_r', 'Yes', 60),
      trade('marcus_tan', 'No', 20),
      trade('sarah_lim', 'Yes', 70),
      trade('priya_s', 'Yes', 40),
      trade('daniel_ng', 'No', 10),
      trade('wei_ling', 'Yes', 30),
    ],
    endsAs: 'open',
    headline: true,
    restingOrder: { outcome: 'Yes', limitPrice: '0.8500', quantity: '40' },
  },
  {
    question: 'Will the CS464 project showcase still be running at 6pm on the day?',
    outcomes: YES_NO,
    closesInDays: 35,
    resolutionCriteria: 'Resolves YES if any team is still presenting at 6pm Singapore time on the day of the CS464 showcase.',
    resolutionSource: { url: 'https://www.smu.edu.sg', label: 'CS464 course page' },
    trades: [
      trade('arjun_k', 'Yes', 25),
      trade('jun_hao', 'No', 30),
      trade('sarah_lim', 'Yes', 40),
      trade('marcus_tan', 'Yes', 15),
      trade('aisyah_r', 'No', 20),
      trade('priya_s', 'Yes', 15),
    ],
    endsAs: 'open',
    restingOrder: { outcome: 'Yes', limitPrice: '0.5500', quantity: '40' },
  },
  {
    question: "Which school will top the medal table at next year's inter-school games?",
    outcomes: ['Business', 'Computing', 'Law', 'Economics', 'Another school'],
    closesInDays: 26,
    resolutionCriteria:
      'Resolves to the school with the most gold medals on the final inter-school games medal table, with total medals breaking a tie. Any school not named resolves as Another school.',
    resolutionSource: { url: 'https://www.smu.edu.sg', label: 'SMU student life' },
    trades: [
      trade('wei_ling', 'Business', 40),
      trade('arjun_k', 'Computing', 35),
      trade('priya_s', 'Law', 20),
      trade('daniel_ng', 'Business', 30),
      trade('jun_hao', 'Computing', 10),
      trade('sarah_lim', 'Business', 25),
      trade('marcus_tan', 'Economics', 5),
    ],
    endsAs: 'open',
    headline: true,
  },
  {
    question: 'Will SMU announce a tuition fee increase for the 2027/28 undergraduate intake?',
    outcomes: YES_NO,
    closesInDays: 90,
    resolutionCriteria:
      "Resolves YES if SMU's published undergraduate tuition fees for the 2027/28 intake are higher than those for the 2026/27 intake, for any citizenship category.",
    resolutionSource: { url: 'https://www.smu.edu.sg', label: 'SMU undergraduate tuition fees' },
    trades: [
      trade('daniel_ng', 'Yes', 40),
      trade('priya_s', 'Yes', 30),
      trade('wei_ling', 'No', 50),
      trade('sarah_lim', 'No', 35),
      trade('arjun_k', 'Yes', 20),
      trade('aisyah_r', 'No', 15),
    ],
    endsAs: 'open',
  },
  {
    question: "Will SMU's Ultimate Frisbee team win a medal at the 2027 IVP Games?",
    outcomes: YES_NO,
    closesInDays: 60,
    resolutionCriteria: 'Resolves YES if SMU wins gold, silver or bronze in the Ultimate Frisbee event of the 2027 Institute-Varsity-Polytechnic Games.',
    resolutionSource: { url: 'https://www.smu.edu.sg', label: 'SMU Sports' },
    trades: [],
    endsAs: 'open',
  },
  {
    question: 'Will the campus green host an open-air movie night before the end of Term 2?',
    outcomes: YES_NO,
    closesInDays: 75,
    resolutionCriteria: 'Resolves YES if a film is screened on the campus green as an event open to all students before the last day of Term 2.',
    resolutionSource: { url: 'https://www.smu.edu.sg', label: 'SMU events calendar' },
    trades: [],
    endsAs: 'open',
  },
  {
    question: 'Will the new café on the campus green open before recess week?',
    outcomes: YES_NO,
    closesInDays: 12,
    resolutionCriteria: 'Resolves YES if the café serves its first customer before the first day of recess week.',
    resolutionSource: { url: 'https://www.smu.edu.sg', label: 'SMU campus news' },
    trades: [trade('marcus_tan', 'Yes', 25), trade('jun_hao', 'No', 15), trade('priya_s', 'Yes', 10)],
    endsAs: 'voided',
    closeReason: 'The café has been postponed. Stopping trading while we check whether this question can still resolve.',
    voidReason: 'The café was postponed with no new date, so the question can no longer resolve either way. Every holder is refunded what they paid.',
  },
  {
    question: 'Will the SMU Libraries extend the laptop loan scheme to postgraduate students this term?',
    outcomes: YES_NO,
    closesInDays: 14,
    resolutionCriteria: 'Resolves YES if the SMU Libraries loan policy lists postgraduate students as eligible to borrow laptops before the end of this term.',
    resolutionSource: { url: 'https://library.smu.edu.sg', label: 'SMU Libraries loan policy' },
    trades: [trade('marcus_tan', 'Yes', 35), trade('aisyah_r', 'Yes', 30), trade('jun_hao', 'No', 25), trade('wei_ling', 'Yes', 45)],
    endsAs: 'closed',
    closeReason: 'The Libraries announced a decision at the town hall. Trading is stopped until the loan policy page is updated.',
  },
  {
    question: 'Will more than 120 employers take part in the Week 7 SMU Career Fair?',
    outcomes: YES_NO,
    closesInDays: 10,
    resolutionCriteria: 'Resolves YES if the final exhibitor list published by the SMU Career Centre names more than 120 employers.',
    resolutionSource: { url: 'https://www.smu.edu.sg', label: 'SMU Career Centre' },
    trades: [
      trade('sarah_lim', 'Yes', 50),
      trade('daniel_ng', 'Yes', 40),
      trade('priya_s', 'No', 20),
      trade('arjun_k', 'Yes', 60),
      trade('marcus_tan', 'No', 15),
    ],
    endsAs: 'pending_resolution',
    closeReason: 'The career fair has taken place. Closing to propose the result.',
    winner: 'Yes',
    evidenceNote: 'The Career Centre published the final exhibitor list after the fair, naming 134 employers.',
  },
  {
    question: "Will the School of Computing and Information Systems' hackathon have more than 300 registrations?",
    outcomes: YES_NO,
    closesInDays: 7,
    resolutionCriteria: 'Resolves YES if the organisers report more than 300 registered participants when registration closes.',
    resolutionSource: { url: 'https://www.smu.edu.sg', label: 'School of Computing and Information Systems hackathon page' },
    trades: [
      trade('jun_hao', 'Yes', 30),
      trade('wei_ling', 'No', 45),
      trade('aisyah_r', 'No', 40),
      trade('sarah_lim', 'Yes', 10),
      trade('daniel_ng', 'No', 30),
    ],
    endsAs: 'approved',
    closeReason: 'Registration for the hackathon has closed. Closing to propose the result.',
    winner: 'No',
    evidenceNote: 'The organisers announced 262 registrations when registration closed.',
  },
  {
    question: 'Will it rain during the open-air concert on campus?',
    outcomes: YES_NO,
    closesInDays: 3,
    resolutionCriteria: 'Resolves YES if rain is recorded at the nearest weather station during the concert, from its first act to its last.',
    resolutionSource: { url: 'https://www.weather.gov.sg', label: 'Meteorological Service Singapore' },
    trades: [
      trade('arjun_k', 'Yes', 40),
      trade('priya_s', 'No', 30),
      trade('marcus_tan', 'Yes', 35),
      trade('jun_hao', 'Yes', 20),
      trade('aisyah_r', 'No', 25),
      trade('wei_ling', 'Yes', 15),
    ],
    endsAs: 'settled',
    closeReason: 'The concert is over. Closing to propose the result.',
    winner: 'Yes',
    evidenceNote: 'Rainfall was recorded at the nearest weather station between 7.40pm and 8.15pm, during the second act.',
  },
]
