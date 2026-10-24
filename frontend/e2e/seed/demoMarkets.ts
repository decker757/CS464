// The demo database's content (#454): who trades, what they trade, and where
// each market ends up. Every market is priced at b = 100 (the e2e helpers pin
// it), so the trades below are sized to move prices well apart: the open
// binaries end near 13%, 33%, 47%, 61%, 75% and 90% Yes, and the four-way
// market near 40 / 24 / 19 / 16. No trader spends more than about 185 credits,
// so any STARTING_CREDITS from 200 up covers them.

export const ADMINS = { creator: 'admin_rachel', approver: 'admin_kumar' }

export const TRADERS = ['wei_ling', 'arjun_k', 'marcus_tan', 'priya_s', 'jun_hao', 'sarah_lim', 'daniel_ng', 'aisyah_r']

export interface DemoTrade {
  trader: string
  outcome: string
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
    | { endsAs: 'open' }
    | { endsAs: 'closed'; closeReason: string }
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
    closesInDays: 9,
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
  },
  {
    question: "Will more than half of SMU's undergraduates vote in the 2027 Students' Association general election?",
    outcomes: YES_NO,
    closesInDays: 21,
    resolutionCriteria:
      "Resolves YES if the turnout the Students' Association publishes for its 2027 general election is above 50% of eligible undergraduates.",
    resolutionSource: { url: 'https://www.smu.edu.sg', label: "SMU Students' Association" },
    trades: [
      trade('marcus_tan', 'No', 60),
      trade('wei_ling', 'Yes', 20),
      trade('daniel_ng', 'No', 70),
      trade('aisyah_r', 'No', 40),
      trade('arjun_k', 'Yes', 15),
      trade('jun_hao', 'No', 55),
    ],
    endsAs: 'open',
  },
  {
    question: "Will SMU's team reach the international rounds of the 2027 Jessup Moot Court Competition?",
    outcomes: YES_NO,
    closesInDays: 40,
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
    closesInDays: 5,
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
  },
  {
    question: 'Will the CS464 project showcase still be running at 6pm on the day?',
    outcomes: YES_NO,
    closesInDays: 2,
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
  },
  {
    question: 'Which school will top the medal table at the 2027 SMU Inter-School Games?',
    outcomes: ['Business', 'Computing', 'Law', 'Economics'],
    closesInDays: 30,
    resolutionCriteria:
      'Resolves to the school with the most gold medals on the final Inter-School Games medal table, with total medals breaking a tie.',
    resolutionSource: { url: 'https://www.smu.edu.sg', label: 'SMU Office of Student Life' },
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
  },
  {
    question: 'Will SMU announce a tuition fee increase for the 2027/28 undergraduate intake?',
    outcomes: YES_NO,
    closesInDays: 60,
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
    closesInDays: 45,
    resolutionCriteria: 'Resolves YES if SMU wins gold, silver or bronze in the Ultimate Frisbee event of the 2027 Institute-Varsity-Polytechnic Games.',
    resolutionSource: { url: 'https://www.smu.edu.sg', label: 'SMU Sports' },
    trades: [],
    endsAs: 'open',
  },
  {
    question: 'Will the SMU Green Lawn host an open-air movie night before the end of Term 2?',
    outcomes: YES_NO,
    closesInDays: 75,
    resolutionCriteria: 'Resolves YES if a film is screened on the SMU Green Lawn as an event open to all students before the last day of Term 2.',
    resolutionSource: { url: 'https://www.smu.edu.sg', label: 'SMU events calendar' },
    trades: [],
    endsAs: 'open',
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
    resolutionCriteria: 'Resolves YES if the final exhibitor list published by the Dato\' Kho Hui Meng Career Centre names more than 120 employers.',
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
    question: "Will the School of Computing's hackathon have more than 300 registrations?",
    outcomes: YES_NO,
    closesInDays: 7,
    resolutionCriteria: 'Resolves YES if the organisers report more than 300 registered participants when registration closes.',
    resolutionSource: { url: 'https://www.smu.edu.sg', label: 'SCIS hackathon page' },
    trades: [
      trade('jun_hao', 'Yes', 30),
      trade('wei_ling', 'No', 45),
      trade('aisyah_r', 'No', 40),
      trade('sarah_lim', 'Yes', 10),
      trade('daniel_ng', 'No', 25),
    ],
    endsAs: 'approved',
    closeReason: 'Registration for the hackathon has closed. Closing to propose the result.',
    winner: 'No',
    evidenceNote: 'The organisers announced 262 registrations when registration closed.',
  },
  {
    question: "Will it rain during the Patron's Day open-air concert on campus?",
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
    evidenceNote: 'Rainfall was recorded at the Newton station between 7.40pm and 8.15pm, during the second act.',
  },
]
